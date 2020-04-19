from meta_mb.baselines.linear_baseline import LinearFeatureBaseline
from meta_mb.meta_envs.point.point_env_1d import MetaPointEnv
from meta_mb.meta_envs.rl2_env import rl2env
from meta_mb.envs.normalized_env import normalize
from meta_mb.algos.ppo import PPO
from meta_mb.trainers.mf_trainer import Trainer
from meta_mb.samplers.meta_samplers.meta_sampler import MetaSampler
from meta_mb.samplers.meta_samplers.rl2_sample_processor import RL2SampleProcessor
from meta_mb.policies.discrete_rnn_policy import DiscreteRNNPolicy
import os
from meta_mb.logger import logger
import json
import numpy as np
from experiment_utils.run_sweep import run_sweep
from meta_mb.utils.utils import set_seed, ClassEncoder
import tensorflow as tf
from babyai.levels.iclr19_levels import Level_GoToIndexedObj 
from babyai.oracle.batch_teacher import BatchTeacher
from babyai.oracle.action_advice import ActionAdvice
from babyai.bot import Bot

INSTANCE_TYPE = 'c4.xlarge'
EXP_NAME = 'rl2_sweep'

def run_experiment(**config):
    exp_dir = os.getcwd() + '/data/' + EXP_NAME
    logger.configure(dir=exp_dir, format_strs=['stdout', 'log', 'csv'], snapshot_mode='last_gap', snapshot_gap=50)
    json.dump(config, open(exp_dir + '/params.json', 'w'), indent=2, sort_keys=True, cls=ClassEncoder)
    set_seed(config['seed'])
    config_sess = tf.ConfigProto()
    config_sess.gpu_options.allow_growth = True
    config_sess.gpu_options.per_process_gpu_memory_fraction = config.get('gpu_frac', 0.95)
    sess = tf.Session(config=config_sess)
    with sess.as_default() as sess:
        baseline = config['baseline']()
        e_new = Level_GoToIndexedObj()
        teacher = BatchTeacher([ActionAdvice(Bot, e_new)])
        e_new.teacher = teacher
        env = rl2env(normalize(e_new))
        obs_dim = e_new.reset().shape[0]
        obs_dim = obs_dim + np.prod(env.action_space.n) + 1 + 1 # obs + act + rew + done
        policy = DiscreteRNNPolicy(
                name="meta-policy",
                obs_dim=obs_dim,
                action_dim=np.prod(env.action_space.n),
                meta_batch_size=config['meta_batch_size'],
                hidden_sizes=config['hidden_sizes'],
                cell_type=config['cell_type']
            )

        sampler = MetaSampler(
            env=env,
            policy=policy,
            rollouts_per_meta_task=config['rollouts_per_meta_task'],
            meta_batch_size=config['meta_batch_size'],
            max_path_length=config['max_path_length'],
            parallel=config['parallel'],
            envs_per_task=1,
        )

        sample_processor = RL2SampleProcessor(
            baseline=baseline,
            discount=config['discount'],
            gae_lambda=config['gae_lambda'],
            normalize_adv=config['normalize_adv'],
            positive_adv=config['positive_adv'],
        )

        algo = PPO(
            policy=policy,
            learning_rate=config['learning_rate'],
            max_epochs=config['max_epochs'],
            backprop_steps=config['backprop_steps'],
        )

        trainer = Trainer(
            algo=algo,
            policy=policy,
            env=env,
            sampler=sampler,
            sample_processor=sample_processor,
            n_itr=config['n_itr'],
            sess=sess,
        )
        trainer.train()


if __name__ == '__main__':

    sweep_params = {
        'algo': ['rl2'],
        'seed': [1, 2, 3],

        'baseline': [LinearFeatureBaseline],
        'env': [MetaPointEnv],
        'meta_batch_size': [100],
        "hidden_sizes": [(64,), (128,)],
        'backprop_steps': [50, 100, 200],
        "rollouts_per_meta_task": [2],
        "parallel": [True],
        "max_path_length": [200],
        "discount": [0.99],
        "gae_lambda": [1.0],
        "normalize_adv": [True],
        "positive_adv": [False],
        "learning_rate": [1e-3],
        "max_epochs": [5],
        "cell_type": ["lstm"],
        "num_minibatches": [1],
        "n_itr": [1000],
        'exp_tag': ['v0'],
        'log_rand': [0, 1, 2, 3],
        #'timeskip': [1, 2, 3, 4]
    }
    run_sweep(run_experiment, sweep_params, EXP_NAME, INSTANCE_TYPE)