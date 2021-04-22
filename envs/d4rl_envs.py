# Allow us to interact wth the D4RLEnv the same way we interact with the TeachableRobotLevels class.
import numpy as np
import gym
import d4rl_content
from gym.spaces import Box, Discrete
from d4rl_content.pointmaze.waypoint_controller import WaypointController
from d4rl.oracle.batch_teacher import BatchTeacher
from oracle.cardinal_teacher import CardinalCorrections
from oracle.direction_teacher import DirectionCorrections
from oracle.waypoint_teacher import WaypointCorrections

from babyai.oracle.dummy_advice import DummyAdvice


class PointMassEnvSimple:
    """
    Parent class to all of the BabyAI envs (TODO: except the most complex levelgen ones currently)
    Provides functions to use with meta-learning, including sampling a task and resetting the same task
    multiple times for multiple runs within the same meta-task.
    """

    def __init__(self, env_name, feedback_type=None, feedback_freq=False, intermediate_reward=False,
                 cartesian_steps=[1], **kwargs):
        self.timesteps = 0
        self.time_limit = 10
        self.target = np.array([0, 0], dtype=np.float32)
        self.pos = np.array([3, 4], dtype=np.float32)
        self.feedback_type = feedback_type
        self.np_random = np.random.RandomState(kwargs.get('seed', 0))  # TODO: seed isn't passed in
        self.teacher_action = np.array(-1)
        self.observation_space = Box(low=np.array([-5, -5]), high=np.array([5, 5]))
        self.action_space = Box(low=np.array([-1, -1]), high=np.array([1, 1]))

    def seed(self, *args, **kwargs):
        pass

    def step(self, action):
        action = np.clip(action, -1, 1)
        if action.shape == (1, 2):
            action = action[0]
        self.pos += action
        rew = -np.linalg.norm(self.target - self.pos) / 10
        self.timesteps += 1
        done = self.timesteps >= self.time_limit
        obs = self.pos
        obs_dict = {'obs': obs}
        success = done and np.linalg.norm(self.target - self.pos) < .49
        info = {}
        info['success'] = success
        info['gave_reward'] = True
        info['teacher_action'] = np.array(-1)
        info['episode_length'] = self.timesteps
        return obs_dict, rew, done, info

    def set_task(self, *args, **kwargs):
        pass  # for compatibility with babyai, which does set tasks

    def reset(self):
        self.pos = np.array([3, 4], dtype=np.float32)
        self.timesteps = 0
        obs_dict = {'obs': self.pos}
        return obs_dict

    def render(self, mode='human'):
        img = np.zeros((100, 100, 3), dtype=np.float32)
        img[48:52, 48:52, :2] = 1
        y = int(min(98, max(2, np.round(self.pos[0] * 10) + 50)))
        x = int(min(98, max(2, np.round(self.pos[1] * 10) + 50)))
        img[y - 2: y + 2, x - 2: x + 2] = 1
        return img * 255

    def vocab(self):  # We don't have vocab
        return [0]


class PointMassEnvSimpleDiscrete:
    """
    Parent class to all of the BabyAI envs (TODO: except the most complex levelgen ones currently)
    Provides functions to use with meta-learning, including sampling a task and resetting the same task
    multiple times for multiple runs within the same meta-task.
    """

    def __init__(self, env_name, feedback_type=None, feedback_freq=False, intermediate_reward=False,
                 cartesian_steps=[1], **kwargs):
        self.timesteps = 0
        self.time_limit = 10
        self.target = np.array([0, 0], dtype=np.float32)
        self.pos = np.array([3, 4], dtype=np.float32)
        self.feedback_type = feedback_type
        self.np_random = np.random.RandomState(kwargs.get('seed', 0))  # TODO: seed isn't passed in
        self.teacher_action = np.array(-1)
        self.observation_space = Box(low=np.array([-5, -5]), high=np.array([5, 5]))
        self.action_space = Discrete(5)
        # TODO: create teachers

    def seed(self, *args, **kwargs):
        pass

    def step(self, action):
        if action == 0:
            action = np.array([-1, 0])
        elif action == 1:
            action = np.array([1, 0])
        elif action == 2:
            action = np.array([0, -1])
        elif action == 3:
            action = np.array([0, 1])
        elif action == 4:
            action = np.array([0, 0])
        else:
            print("uh oh")
        self.pos += action
        rew = -np.linalg.norm(self.target - self.pos) / 10
        self.timesteps += 1
        done = self.timesteps >= self.time_limit
        obs = self.pos
        obs_dict = {'obs': obs}
        success = done and np.linalg.norm(self.target - self.pos) < .49
        info = {}
        info['success'] = success
        info['gave_reward'] = True
        info['teacher_action'] = np.array(-1)
        info['episode_length'] = self.timesteps
        return obs_dict, rew, done, info

    def set_task(self, *args, **kwargs):
        pass  # for compatibility with babyai, which does set tasks

    def reset(self):
        self.pos = np.array([3, 4], dtype=np.float32)
        self.timesteps = 0
        obs_dict = {'obs': self.pos}
        return obs_dict

    def render(self, mode='human'):
        img = np.zeros((100, 100, 3), dtype=np.float32)
        img[48:52, 48:52, :2] = 1
        y = int(min(98, max(2, np.round(self.pos[0] * 10) + 50)))
        x = int(min(98, max(2, np.round(self.pos[1] * 10) + 50)))
        img[y - 2: y + 2, x - 2: x + 2] = 1
        return img * 255

    def vocab(self):  # We don't have vocab
        return [0]


class D4RLEnv:
    """
    Parent class to all of the BabyAI envs (TODO: except the most complex levelgen ones currently)
    Provides functions to use with meta-learning, including sampling a task and resetting the same task
    multiple times for multiple runs within the same meta-task.
    """

    def __init__(self, env_name, reward_type='dense', feedback_type=None, feedback_freq=False,
                 cartesian_steps=[1], **kwargs):
        self.reward_type = reward_type
        self.steps_since_recompute = 0
        self._wrapped_env = gym.envs.make(env_name, reset_target=True, reward_type=reward_type)
        self.feedback_type = feedback_type
        self.np_random = np.random.RandomState(kwargs.get('seed', 0))  # TODO: seed isn't passed in
        self.teacher_action = self.action_space.sample() * 0 - 1
        if self.reward_type in ['oracle_action', 'oracle_dist']:
            self.waypoint_controller = WaypointController(self.get_maze())
        if feedback_type is not None and not 'none' in feedback_type:
            teachers = {}
            if type(cartesian_steps) is int:
                cartesian_steps = [cartesian_steps]
            assert len(cartesian_steps) == 1 or len(cartesian_steps) == len(feedback_type), \
                "you must provide either one cartesian_steps value for all teachers or one per teacher"
            assert len(feedback_freq) == 1 or len(feedback_freq) == len(feedback_type), \
                "you must provide either one feedback_freq value for all teachers or one per teacher"
            if len(cartesian_steps) == 1:
                cartesian_steps = [cartesian_steps[0]] * len(feedback_type)
            if len(feedback_freq) == 1:
                feedback_freq = [feedback_freq[0]] * len(feedback_type)
            for ft, ff, cs in zip(feedback_type, feedback_freq, cartesian_steps):
                if ft == 'None':
                    teachers[ft] = DummyAdvice(self)
                elif ft == 'Cardinal':
                    teachers[ft] = CardinalCorrections(self, feedback_frequency=ff, cartesian_steps=cs,
                                                       controller=self.waypoint_controller)
                elif ft == 'Waypoint':
                    teachers[ft] = WaypointCorrections(self, feedback_frequency=ff, cartesian_steps=cs,
                                                       controller=self.waypoint_controller)
                elif ft == 'Direction':
                    teachers[ft] = DirectionCorrections(self, feedback_frequency=ff, cartesian_steps=cs,
                                                        controller=self.waypoint_controller)
            teacher = BatchTeacher(teachers)
        else:
            teacher = None
        self.teacher = teacher
        # TODO: create teachers

    def get_target(self):
        raise NotImplementedError

    def get_pos(self):
        raise NotImplementedError

    def get_vel(self):
        raise NotImplementedError

    def get_maze(self):
        raise NotImplementedError

    def add_feedback(self, obs_dict):
        if self.teacher is not None and not 'None' in self.teacher.teachers:
            advice = self.teacher.give_feedback(self)
            obs_dict.update(advice)
        return obs_dict

    def step(self, action):
        obs, rew, done, info = self._wrapped_env.step(action)
        if self.reward_type == 'oracle_action':
            act, done = self.waypoint_controller.get_action(self.get_pos(), self.get_vel(), self.get_target())
            rew = -np.linalg.norm(action - act) / 100 + .03  # scale so it's not too big and is always positive
        elif self.reward_type == 'oracle_dist':
            self.waypoint_controller.new_target(self.get_pos(), self.get_target())
            # Distance between each 2 points
            start_points = [self.get_pos()] + self.waypoint_controller.waypoints[:-1]
            end_points = self.waypoint_controller.waypoints
            distance = sum([np.linalg.norm(end - start) for start, end in zip(start_points, end_points)])
            rew = - distance / 100
        obs_dict = {}
        obs_dict["obs"] = obs
        obs_dict = self.add_feedback(obs_dict)
        self.done = done

        target = self.get_target()
        agent_pos = obs[:2]
        success = done and np.linalg.norm(target - agent_pos) < .5
        info = {}
        info['success'] = success
        info['gave_reward'] = True
        info['teacher_action'] = np.array(-1)
        info['episode_length'] = self._wrapped_env._elapsed_steps

        if hasattr(self, 'teacher') and self.teacher is not None:
            # Even if we use multiple teachers, presumably they all relate to one underlying path.
            # We can log what action is the next one on this path (currently in teacher.next_action).
            info['teacher_action'] = self.get_teacher_action()
            self.teacher.step(self)
            # TODO: consider adding `followed`
            # for k, v in self.teacher.success_check(obs, action, self.oracle).items():
            #     info[f'followed_{k}'] = v
            info['teacher_error'] = float(self.teacher.get_last_step_error())
            # Update the observation with the teacher's new feedback
            self.teacher_action = self.get_teacher_action()
        # print("Waypoint", obs_dict['Waypoint'], obs_dict['gave_Waypoint'])  # TODO: potentially something odd here?
        return obs_dict, rew, done, info

    def get_teacher_action(self):
        if hasattr(self, 'teacher') and self.teacher is not None:
            # Even if we use multiple teachers, presumably they all relate to one underlying path.
            # We can log what action is the next one on this path (currently in teacher.next_action).
            if isinstance(self.teacher, BatchTeacher):
                # Sanity check that all teachers have the same underlying path
                first_action = list(self.teacher.teachers.values())[0].next_action
                for teacher_name, teacher in self.teacher.teachers.items():
                    if not np.array_equal(first_action, teacher.next_action):
                        print(f"Teacher Actions didn't match {[(k, int(v.next_action)) for k,v in self.teacher.teachers.items()]}")
                return list(self.teacher.teachers.values())[0].next_action
            else:
                return np.array([self.teacher.next_action], dtype=np.float32)
        return None

    def set_task(self, *args, **kwargs):
        pass  # for compatibility with babyai, which does set tasks

    def reset(self):
        obs = self._wrapped_env.reset()
        obs_dict = {'obs': obs}
        self.steps_since_recompute = 0
        if self.reward_type in ['oracle_action', 'oracle_dist']:
            self.waypoint_controller = WaypointController(self.get_maze())
        self.waypoint_controller.new_target(self.get_pos(), self.get_target())
        if hasattr(self, 'teacher') and self.teacher is not None:
            self.teacher.reset(self)
        self.teacher_action = self.get_teacher_action()
        obs_dict = self.add_feedback(obs_dict)
        return obs_dict

    def vocab(self):  # We don't have vocab
        return [0]

    def __getattr__(self, attr):
        """
        If normalized env does not have the attribute then call the attribute in the wrapped_env
        Args:
            attr: attribute to get

        Returns:
            attribute of the wrapped_env

        # """
        try:
            if attr == '__len__':
                return None
            results = self.__getattribute__(attr)
            return results
        except:
            orig_attr = self._wrapped_env.__getattribute__(attr)

            if callable(orig_attr):
                def hooked(*args, **kwargs):
                    result = orig_attr(*args, **kwargs)
                    return result

                return hooked
            else:
                return orig_attr


class PointMassEnv(D4RLEnv):
    def __init__(self, *args, **kwargs):
        super(PointMassEnv, self).__init__(*args, **kwargs)
        # Adding goal
        self.observation_space = Box(low=-float('inf'), high=float('inf'), shape=(6,))

    def get_target(self):
        return self._wrapped_env.get_target()

    def get_maze(self):
        return self._wrapped_env.get_maze()

    def get_pos(self):
        return self._wrapped_env.get_sim().data.qpos

    def get_vel(self):
        return self._wrapped_env.get_sim().data.qvel

    def step(self, action):
        obs_dict, rew, done, info = super().step(action)
        obs_dict['obs'] = np.concatenate([obs_dict['obs'], self.get_target()])
        if self.reward_type == 'dense':
            rew = rew / 10 - .01
        return obs_dict, rew, done, info

    def reset(self):
        obs_dict = super().reset()
        obs_dict['obs'] = np.concatenate([obs_dict['obs'], self.get_target()])
        return obs_dict


class AntEnv(D4RLEnv):
    def get_target(self):
        return np.array(self._wrapped_env.xy_to_rowcolcontinuous(self._wrapped_env.get_target()))

    def get_maze(self):
        return self._wrapped_env.get_maze()  # TODO: I think TimeLimit will kill this

    def get_pos(self):
        return np.array(self._wrapped_env.xy_to_rowcolcontinuous(self._wrapped_env.get_xy()))

    def get_vel(self):
        return np.array([0, 0])  # TODO: is there a better option?

    def render(self, *args, **kwargs):
        return self._wrapped_env.render(*args, **kwargs)

    def step(self, action):
        obs_dict, rew, done, info = super().step(action)
        if self.reward_type == 'dense':
            rew = rew / 100 + .1
        return obs_dict, rew, done, info