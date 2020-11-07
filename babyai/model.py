import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions.categorical import Categorical
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
import babyai.rl
from babyai.rl.utils.supervised_losses import required_heads
import numpy as np
from babyai.rl.utils.dictlist import DictList

# From https://github.com/ikostrikov/pytorch-a2c-ppo-acktr/blob/master/model.py
def initialize_parameters(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        m.weight.data.normal_(0, 1)
        m.weight.data *= 1 / torch.sqrt(m.weight.data.pow(2).sum(1, keepdim=True))
        if m.bias is not None:
            m.bias.data.fill_(0)


# Inspired by FiLMedBlock from https://arxiv.org/abs/1709.07871
class FiLM(nn.Module):
    def __init__(self, in_features, out_features, in_channels, imm_channels):
        super().__init__()
        self.conv1 = nn.Conv2d(
            in_channels=in_channels, out_channels=imm_channels,
            kernel_size=(3, 3), padding=1)
        # self.bn1 = nn.BatchNorm2d(imm_channels)
        self.conv2 = nn.Conv2d(
            in_channels=imm_channels, out_channels=out_features,
            kernel_size=(3, 3), padding=1)
        # self.bn2 = nn.BatchNorm2d(out_features)

        self.weight = nn.Linear(in_features, out_features)
        self.bias = nn.Linear(in_features, out_features)

        self.apply(initialize_parameters)

    def forward(self, x, y):
        # x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.conv1(x))
        x = self.conv2(x)
        weight = self.weight(y).unsqueeze(2).unsqueeze(3)
        bias = self.bias(y).unsqueeze(2).unsqueeze(3)
        out = x * weight + bias
        # return F.relu(self.bn2(out))
        return F.relu(out)


class ImageBOWEmbedding(nn.Module):
   def __init__(self, max_value, embedding_dim):
       super().__init__()
       self.max_value = max_value
       self.embedding_dim = embedding_dim
       self.embedding = nn.Embedding(3 * max_value, embedding_dim)
       self.apply(initialize_parameters)

   def forward(self, inputs):
       offsets = torch.Tensor([0, self.max_value, 2 * self.max_value]).to(inputs.device)
       inputs = (inputs + offsets[None, :, None, None]).long()
       return self.embedding(inputs).sum(1).permute(0, 3, 1, 2)


class ACModel(nn.Module, babyai.rl.RecurrentACModel):
    def __init__(self, action_space, env,
                 image_dim=128, memory_dim=128, instr_dim=128,
                 use_instr=False, lang_model="gru", use_memory=False,
                 arch="bow_endpool_res", aux_info=None, advice_dim=128,
                 advice_size=-1, num_modules=1):
        super().__init__()

        endpool = 'endpool' in arch
        use_bow = 'bow' in arch
        pixel = 'pixel' in arch
        self.res = 'res' in arch

        # Decide which components are enabled
        self.use_instr = use_instr
        self.use_memory = use_memory
        self.arch = arch
        self.lang_model = lang_model
        self.aux_info = aux_info
        if self.res and image_dim != 128:
            raise ValueError(f"image_dim is {image_dim}, expected 128")
        self.image_dim = image_dim
        self.memory_dim = memory_dim
        self.instr_dim = instr_dim

        self.action_space = action_space
        self.env = env
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.advice_dim = advice_dim
        self.advice_size = advice_size
        self.num_modules = num_modules

        for part in self.arch.split('_'):
            if part not in ['original', 'bow', 'pixels', 'endpool', 'res']:
                raise ValueError("Incorrect architecture name: {}".format(self.arch))

        self.image_conv = nn.Sequential(*[
            *([ImageBOWEmbedding(147, 128)] if use_bow else []),
            *([nn.Conv2d(
                in_channels=3, out_channels=128, kernel_size=(8, 8),
                stride=8, padding=0)] if pixel else []),
            nn.Conv2d(
                in_channels=128 if use_bow or pixel else 3, out_channels=128,
                kernel_size=(3, 3) if endpool else (2, 2), stride=1, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(),
            *([] if endpool else [nn.MaxPool2d(kernel_size=(2, 2), stride=2)]),
            nn.Conv2d(in_channels=128, out_channels=128, kernel_size=(3, 3), padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(),
            *([] if endpool else [nn.MaxPool2d(kernel_size=(2, 2), stride=2)])
        ])
        self.film_pool = nn.MaxPool2d(kernel_size=(7, 7) if endpool else (2, 2), stride=2)

        if self.advice_size > 0:
            self.advice_embedding = nn.Sequential(
                nn.Linear(self.advice_size, self.advice_dim),
                nn.Sigmoid(),
            )

        # Define instruction embedding
        if self.use_instr:
            if self.lang_model in ['gru', 'bigru', 'attgru']:
                self.word_embedding = nn.Embedding(len(env.vocab()), self.instr_dim)
                if self.lang_model in ['gru', 'bigru', 'attgru']:
                    gru_dim = self.instr_dim
                    if self.lang_model in ['bigru', 'attgru']:
                        gru_dim //= 2
                    self.instr_rnn = nn.GRU(
                        self.instr_dim, gru_dim, batch_first=True,
                        bidirectional=(self.lang_model in ['bigru', 'attgru']))
                    self.final_instr_dim = self.instr_dim + self.advice_dim
                else:
                    kernel_dim = 64
                    kernel_sizes = [3, 4]
                    self.instr_convs = nn.ModuleList([
                        nn.Conv2d(1, kernel_dim, (K, self.instr_dim)) for K in kernel_sizes])
                    self.final_instr_dim = kernel_dim * len(kernel_sizes) + self.advice_dim

            if self.lang_model == 'attgru':
                self.memory2key = nn.Linear(self.memory_size, self.final_instr_dim)

            self.controllers = []
            for ni in range(self.num_modules):
                mod = FiLM(
                    in_features=self.final_instr_dim,
                    out_features=128 if ni < self.num_modules - 1 else self.image_dim,
                    in_channels=128, imm_channels=128)
                self.controllers.append(mod)
                self.add_module('FiLM_' + str(ni), mod)

        # Define memory and resize image embedding
        self.embedding_size = self.image_dim
        if self.use_memory:
            self.memory_rnn = nn.LSTMCell(self.image_dim, self.memory_dim)
            self.embedding_size = self.semi_memory_size

        # Define actor's model
        self.actor = nn.Sequential(
            nn.Linear(self.embedding_size + self.advice_dim, 64),
            nn.Tanh(),
            nn.Linear(64, action_space.n)
        )

        # Define critic's model
        self.critic = nn.Sequential(
            nn.Linear(self.embedding_size + self.advice_dim, 64),
            nn.Tanh(),
            nn.Linear(64, 1)
        )

        # Initialize parameters correctly
        self.apply(initialize_parameters)

        # Define head for extra info
        if self.aux_info:
            self.extra_heads = None
            self.add_heads()

    def add_heads(self):
        '''
        When using auxiliary tasks, the environment yields at each step some binary, continous, or multiclass
        information. The agent needs to predict those information. This function add extra heads to the model
        that output the predictions. There is a head per extra information (the head type depends on the extra
        information type).
        '''
        self.extra_heads = nn.ModuleDict()
        for info in self.aux_info:
            if required_heads[info] == 'binary':
                self.extra_heads[info] = nn.Linear(self.embedding_size, 1)
            elif required_heads[info].startswith('multiclass'):
                n_classes = int(required_heads[info].split('multiclass')[-1])
                self.extra_heads[info] = nn.Linear(self.embedding_size, n_classes)
            elif required_heads[info].startswith('continuous'):
                if required_heads[info].endswith('01'):
                    self.extra_heads[info] = nn.Sequential(nn.Linear(self.embedding_size, 1), nn.Sigmoid())
                else:
                    raise ValueError('Only continous01 is implemented')
            else:
                raise ValueError('Type not supported')
            # initializing these parameters independently is done in order to have consistency of results when using
            # supervised-loss-coef = 0 and when not using any extra binary information
            self.extra_heads[info].apply(initialize_parameters)

    def add_extra_heads_if_necessary(self, aux_info):
        '''
        This function allows using a pre-trained model without aux_info and add aux_info to it and still make
        it possible to finetune.
        '''
        try:
            if not hasattr(self, 'aux_info') or not set(self.aux_info) == set(aux_info):
                self.aux_info = aux_info
                self.add_heads()
        except Exception:
            raise ValueError('Could not add extra heads')

    @property
    def memory_size(self):
        return 2 * self.semi_memory_size

    @property
    def semi_memory_size(self):
        return self.memory_dim

    def reset(self, dones=None):
        batch_len = 1 if dones is None else len(dones)
        self.memory = torch.zeros([batch_len, self.memory_size], device=self.device)

    def get_actions(self, obs, training=False):
        if type(obs) is list:
            obs = torch.FloatTensor(np.stack(obs, axis=0)).to(self.device)
        else:
            # Double check we have a dictlist and that we have a batch dim
            assert type(obs) is DictList, type(obs)
            assert len(obs.obs.shape) == 4, obs.obs.shape
        action_list = [[] for _ in range(len(obs))]
        info_list = [[] for _ in range(len(obs))]
        for t in range(len(obs[0])):
            obs_t = obs[:, t]
            action, info = self.get_actions_t(obs_t, training)
            for i in range(len(info_list)):
                action_list[i].append([action[i]])
                info_list[i].append(info[i])
        actions = np.array(action_list)
        return actions, info_list

    def get_actions_t(self, obs, training=False):  # TODO: this feels like a good place for a DictList
        if training:
            self.train()
        else:
            self.eval()
        dist, info = self(obs, self.memory)
        self.memory = info['memory']
        info_list = []
        probs = info['probs'].detach().cpu().numpy()
        actions = dist.sample()
        log_probs = dist.log_prob(actions).detach().cpu().numpy()
        values = info['value'].detach().cpu().numpy()
        memory = info['memory'].detach().cpu().numpy()
        actions = actions.detach().cpu().numpy()

        for i in range(len(probs)):
            info_list.append({
                "memory": memory[i],
                "value": values[i],
                "log_prob": log_probs[i],
                "probs": probs[i],
            })
        return actions, info_list


    def forward(self, obs, memory, instr_embedding=None):
        instruction_vector = obs.instr.long()
        if self.advice_size > 0:
            advice_vector = obs.advice
            advice_embedding = self._get_advice_embedding(advice_vector)
        img_vector = obs.obs
        if self.use_instr and instr_embedding is None:
            instr_embedding = self._get_instr_embedding(instruction_vector)
        if self.use_instr and self.lang_model == "attgru":
            # outputs: B x L x D
            # memory: B x M
            mask = (instruction_vector != 0).float()
            # The mask tensor has the same length as obs.instr, and
            # thus can be both shorter and longer than instr_embedding.
            # It can be longer if instr_embedding is computed
            # for a subbatch of obs.instr.
            # It can be shorter if obs.instr is a subbatch of
            # the batch that instr_embeddings was computed for.
            # Here, we make sure that mask and instr_embeddings
            # have equal length along dimension 1.
            mask = mask[:, :instr_embedding.shape[1]]
            instr_embedding = instr_embedding[:, :mask.shape[1]]

            keys = self.memory2key(memory)
            pre_softmax = (keys[:, None, :] * instr_embedding).sum(2) + 1000 * mask
            attention = F.softmax(pre_softmax, dim=1)
            instr_embedding = (instr_embedding * attention[:, :, None]).sum(1)

        # Add the teacher's advice into the instruction
        if self.use_instr and self.advice_size > 0:
            instr_embedding = torch.cat([instr_embedding, advice_embedding], dim=1)

        x = torch.transpose(torch.transpose(img_vector, 1, 3), 2, 3)

        if 'pixel' in self.arch:
            x /= 256.0
        x = self.image_conv(x)
        if self.use_instr:
            for controller in self.controllers:
                out = controller(x, instr_embedding)
                if self.res:
                    out += x
                x = out
        x = F.relu(self.film_pool(x))
        x = x.reshape(x.shape[0], -1)

        if self.use_memory:
            hidden = (memory[:, :self.semi_memory_size], memory[:, self.semi_memory_size:])
            hidden = self.memory_rnn(x, hidden)
            embedding = hidden[0]
            memory = torch.cat(hidden, dim=1)
        else:
            embedding = x

        if self.advice_size > 0:
            embedding = torch.cat([embedding, advice_embedding], dim=1)

        x = self.actor(embedding)
        dist = Categorical(logits=F.log_softmax(x, dim=1))
        probs = F.softmax(x, dim=1)

        x = self.critic(embedding)
        value = x.squeeze(1)

        info = {
            "probs": probs,
            "value": value,
            "memory": memory,
        }

        return dist, info

    def _get_instr_embedding(self, instr):
        lengths = (instr != 0).sum(1).long()
        if self.lang_model == 'gru':
            out, _ = self.instr_rnn(self.word_embedding(instr))
            hidden = out[range(len(lengths)), lengths-1, :]
            return hidden

        elif self.lang_model in ['bigru', 'attgru']:
            masks = (instr != 0).float()
            # Make sure we don't have an entire trajectory of zeros.
            # doing this doesn't matter since we should be masking out these transitions anyway.
            # all_zeros = masks.sum(dim=1) == 0
            # instr[all_zeros] = instr[all_zeros] + 1
            # lengths = (instr != 0).sum(1).long()

            if lengths.shape[0] > 1:
                seq_lengths, perm_idx = lengths.sort(0, descending=True)
                iperm_idx = torch.LongTensor(perm_idx.shape).fill_(0)
                if instr.is_cuda: iperm_idx = iperm_idx.cuda()
                for i, v in enumerate(perm_idx):
                    iperm_idx[v.data] = i

                inputs = self.word_embedding(instr.long())
                inputs = inputs[perm_idx]
                inputs = pack_padded_sequence(inputs, seq_lengths.data.cpu().numpy(), batch_first=True)

                outputs, final_states = self.instr_rnn(inputs)
            else:
                instr = instr[:, 0:lengths[0]]
                outputs, final_states = self.instr_rnn(self.word_embedding(instr))
                iperm_idx = None
            final_states = final_states.transpose(0, 1).contiguous()
            final_states = final_states.view(final_states.shape[0], -1)
            if iperm_idx is not None:
                outputs, _ = pad_packed_sequence(outputs, batch_first=True)
                outputs = outputs[iperm_idx]
                final_states = final_states[iperm_idx]

            return outputs if self.lang_model == 'attgru' else final_states

        else:
            ValueError("Undefined instruction architecture: {}".format(self.use_instr))


    def _get_advice_embedding(self, advice):
        return self.advice_embedding(advice.float())
