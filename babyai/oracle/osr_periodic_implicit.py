import numpy as np
import pickle as pkl
from babyai.oracle.off_sparse_random_easy import OSREasy


class OSRPeriodicImplicit(OSREasy):
    def __init__(self, *args, **kwargs):
        self.feedback_active = False
        super(OSRPeriodicImplicit, self).__init__(*args, **kwargs)

    def compute_feedback(self, oracle, last_action=-1):
        """
        Return the expert action from the previous timestep.
        """
        # Copy so we don't mess up the state of the real oracle
        oracle_copy = pkl.loads(pkl.dumps(oracle))
        self.step_ahead(oracle_copy, last_action=last_action)
        env = oracle.mission
        return self.generic_feedback(env, offset=True)

    def feedback_condition(self, oracle, action):
        """
        Returns true when we should give feedback, which happens every time the agent messes up
        """
        env = oracle.mission
        # If we achieved our goal or have spen long enough chasing this one, pick a new one. We may or may not show it.
        if (self.steps_since_lastfeedback % self.num_steps == 0) or np.array_equal(env.agent_pos, self.goal_coords):
            self.steps_since_lastfeedback = 0
            give_feedback = np.random.uniform() < .5
            self.feedback_active = give_feedback
            if not give_feedback:  # If we're not giving feedback, set self.goal_coords ourselves
                self.step_ahead(oracle)
            return give_feedback
        return False
