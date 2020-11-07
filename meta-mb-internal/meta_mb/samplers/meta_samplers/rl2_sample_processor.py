from meta_mb.samplers.base import SampleProcessor
import numpy as np
import copy


class RL2SampleProcessor(SampleProcessor):

    def process_samples(self, paths_meta_batch, log=False, log_prefix='', log_teacher=True):
        """
        Processes sampled paths. This involves:
            - computing discounted rewards (returns)
            - fitting baseline estimator using the path returns and predicting the return baselines
            - estimating the advantages using GAE (+ advantage normalization id desired)
            - stacking the path data
            - logging statistics of the paths

        Args:
            paths_meta_batch (dict): A list of dict of lists, size: [meta_batch_size] x (batch_size) x [5] x (max_path_length)
            log (boolean): indicates whether to log
            log_prefix (str): prefix for the logging keys

        Returns:
            (list of dicts) : Processed sample data among the meta-batch; size: [meta_batch_size] x [7] x (batch_size x max_path_length)
        """
        assert isinstance(paths_meta_batch, dict), 'paths must be a dict'
        original_paths = paths_meta_batch

        samples_data_meta_batch = []
        all_paths = []

        for meta_task, paths in paths_meta_batch.items():
            # fits baseline, compute advantages and stack path data
            samples_data, paths = self._compute_samples_data(
                paths)  # TODO: Is RL^2 Optimizing for the N paths in a trial?

            samples_data_meta_batch.append(samples_data)
            all_paths.extend(paths)

        observations, actions, rewards, dones, returns, advantages, env_infos, agent_infos = \
            self._stack_path_data(copy.deepcopy(samples_data_meta_batch))

        # 8) log statistics if desired
        self._log_path_stats(all_paths, log=log, log_prefix=log_prefix, log_teacher=log_teacher)
        samples_data = dict(
            observations=observations,
            actions=actions,
            rewards=rewards,
            dones=dones,
            returns=returns,
            advantages=advantages,
            env_infos=env_infos,
            agent_infos=agent_infos,
            avg_reward=np.mean([sum(path["rewards"]) for path in all_paths])
        )
        return samples_data
