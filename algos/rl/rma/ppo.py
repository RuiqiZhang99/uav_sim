import os
import warnings
from typing import Any, Callable, Dict, Optional, Type, Union

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
#
import pandas as pd
import scipy
import torch as th
from gym import spaces
from mpl_toolkits.mplot3d import Axes3D
#
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.common.type_aliases import GymEnv, MaybeCallback
from stable_baselines3.common.utils import explained_variance, get_schedule_fn
from torch.nn import functional as F

#
import wandb
import sys
import os

# sys.path.append(os.environ['FLIGHTMARE_PATH']+'/flightpy')
from algos.rl.rma.on_policy_algorithm import OnPolicyAlgorithm
from algos.rl.rma.rma_util import plot3d_traj, traj_rollout


class PPO(OnPolicyAlgorithm):
    """
    Proximal Policy Optimization algorithm (PPO) (clip version)

    Paper: https://arxiv.org/abs/1707.06347
    Code: This implementation borrows code from OpenAI Spinning Up (https://github.com/openai/spinningup/)
    https://github.com/ikostrikov/pytorch-a2c-ppo-acktr-gail and
    and Stable Baselines (PPO2 from https://github.com/hill-a/stable-baselines)

    Introduction to PPO: https://spinningup.openai.com/en/latest/algorithms/ppo.html

    :param policy: The policy model to use (MlpPolicy, CnnPolicy, ...)
    :param env: The environment to learn from (if registered in Gym, can be str)
    :param learning_rate: The learning rate, it can be a function
        of the current progress remaining (from 1 to 0)
    :param n_steps: The number of steps to run for each environment per update
        (i.e. rollout buffer size is n_steps * n_envs where n_envs is number of environment copies running in parallel)
        NOTE: n_steps * n_envs must be greater than 1 (because of the advantage normalization)
        See https://github.com/pytorch/pytorch/issues/29372
    :param batch_size: Minibatch size
    :param n_epochs: Number of epoch when optimizing the surrogate loss
    :param gamma: Discount factor
    :param gae_lambda: Factor for trade-off of bias vs variance for Generalized Advantage Estimator
    :param clip_range: Clipping parameter, it can be a function of the current progress
        remaining (from 1 to 0).
    :param clip_range_vf: Clipping parameter for the value function,
        it can be a function of the current progress remaining (from 1 to 0).
        This is a parameter specific to the OpenAI implementation. If None is passed (default),
        no clipping will be done on the value function.
        IMPORTANT: this clipping depends on the reward scaling.
    :param ent_coef: Entropy coefficient for the loss calculation
    :param vf_coef: Value function coefficient for the loss calculation
    :param min_BC_coef: Max BC coefficient for the behavior cloning loss calculation
    :param BC_scale: BC coefficient scale factor
    :param BC_alpha: BC coefficient decay rate
    :param max_grad_norm: The maximum value for the gradient clipping
    :param use_sde: Whether to use generalized State Dependent Exploration (gSDE)
        instead of action noise exploration (default: False)
    :param sde_sample_freq: Sample a new noise matrix every n steps when using gSDE
        Default: -1 (only sample at the beginning of the rollout)
    :param target_kl: Limit the KL divergence between updates,
        because the clipping is not enough to prevent large update
        see issue #213 (cf https://github.com/hill-a/stable-baselines/issues/213)
        By default, there is no limit on the kl div.
    :param tensorboard_log: the log location for tensorboard (if None, no logging)
    :param create_eval_env: Whether to create a second environment that will be
        used for evaluating the agent periodically. (Only available when passing string for the environment)
    :param policy_kwargs: additional arguments to be passed to the policy on creation
    :param verbose: the verbosity level: 0 no output, 1 info, 2 debug
    :param seed: Seed for the pseudo random generators
    :param device: Device (cpu, cuda, ...) on which the code should be run.
        Setting it to auto, the code will be run on the GPU if possible.
    :param _init_setup_model: Whether or not to build the network at the creation of the instance
    """

    def __init__(
        self,
        policy: Union[str, Type[ActorCriticPolicy]],
        env: Union[GymEnv, str],
        learning_rate: Union[float, Callable] = 3e-4,
        n_steps: int = 2048,
        use_tanh_act: bool = True,
        batch_size: Optional[int] = 64,
        n_epochs: int = 10,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_range: Union[float, Callable] = 0.2,
        clip_range_vf: Union[None, float, Callable] = None,
        ent_coef: float = 0.0,
        vf_coef: float = 0.5,
        min_BC_coef: float = 0.5,
        BC_scale: float = 1.0,
        BC_alpha: float = 0.999,
        max_grad_norm: float = 0.5,
        use_sde: bool = False,
        sde_sample_freq: int = -1,
        target_kl: Optional[float] = None,
        tensorboard_log: Optional[str] = None,
        create_eval_env: bool = False,
        eval_env: Union[GymEnv, str] = None,
        policy_kwargs: Optional[Dict[str, Any]] = None,
        verbose: int = 0,
        seed: Optional[int] = None,
        device: Union[th.device, str] = "auto",
        env_cfg: str = None,
        _init_setup_model: bool = True,
        _init_setup_policy: bool = True,
    ):

        super(PPO, self).__init__(
            policy,
            env,
            learning_rate=learning_rate,
            n_steps=n_steps,
            gamma=gamma,
            gae_lambda=gae_lambda,
            ent_coef=ent_coef,
            use_tanh_act=use_tanh_act,
            vf_coef=vf_coef,
            max_grad_norm=max_grad_norm,
            use_sde=use_sde,
            sde_sample_freq=sde_sample_freq,
            tensorboard_log=tensorboard_log,
            policy_kwargs=policy_kwargs,
            verbose=verbose,
            device=device,
            create_eval_env=create_eval_env,
            eval_env=eval_env,
            seed=seed,
            _init_setup_model=False,
            _init_setup_policy = _init_setup_policy,
            supported_action_spaces=(
                spaces.Box,
                spaces.Discrete,
                spaces.MultiDiscrete,
                spaces.MultiBinary,
            ),
        )
        if self.env is not None:
            # Check that `n_steps * n_envs > 1` to avoid NaN
            # when doing advantage normalization
            buffer_size = self.env.num_envs * self.n_steps
            assert (
                buffer_size > 1
            ), f"`n_steps * n_envs` must be greater than 1. Currently n_steps={self.n_steps} and n_envs={self.env.num_envs}"
            # Check that the rollout buffer size is a multiple of the mini-batch size
            untruncated_batches = buffer_size // batch_size
            if buffer_size % batch_size > 0:
                warnings.warn(
                    f"You have specified a mini-batch size of {batch_size},"
                    f" but because the `RolloutBuffer` is of size `n_steps * n_envs = {buffer_size}`,"
                    f" after every {untruncated_batches} untruncated mini-batches,"
                    f" there will be a truncated mini-batch of size {buffer_size % batch_size}\n"
                    f"We recommend using a `batch_size` that is a multiple of `n_steps * n_envs`.\n"
                    f"Info: (n_steps={self.n_steps} and n_envs={self.env.num_envs})"
                )
        self.batch_size = batch_size
        self.n_epochs = n_epochs
        self.clip_range = clip_range
        self.clip_range_vf = clip_range_vf
        self.target_kl = target_kl
        
        self.min_BC_coef = min_BC_coef
        self.BC_alpha = BC_alpha
        self.BC_scale = BC_scale

        self.env_cfg = env_cfg
        if _init_setup_model:
            self._setup_model()

    def _setup_model(self) -> None:
        super(PPO, self)._setup_model()

        # Initialize schedules for policy/value clipping
        self.clip_range = get_schedule_fn(self.clip_range)
        if self.clip_range_vf is not None:
            if isinstance(self.clip_range_vf, (float, int)):
                assert self.clip_range_vf > 0, (
                    "`clip_range_vf` must be positive, "
                    "pass `None` to deactivate vf clipping"
                )

            self.clip_range_vf = get_schedule_fn(self.clip_range_vf)

    def train(self) -> None:
        """
        Update policy using the currently gathered rollout buffer.
        """
        # Update optimizer learning rate
        self._update_learning_rate(self.policy.optimizer)
        # Compute current clip range
        clip_range = self.clip_range(self._current_progress_remaining)
        # Optional: clip range for the value function
        if self.clip_range_vf is not None:
            clip_range_vf = self.clip_range_vf(self._current_progress_remaining)

        entropy_losses, all_kl_divs = [], []
        pg_losses, value_losses = [], []
        BC_losses = []
        clip_fractions = []

        # train for n_epochs epochs
        for epoch in range(self.n_epochs):
            approx_kl_divs = []
            
            ## get expert extrinsics rollout_data.expert_extrinsics
            ## get student prediction of extrinscis -- (use student.get_extrinsics(appropriate observation))
            ## mse_loss MSE(expert_extrinsics, student_extrinsics)
            ## call backward
            # Do a complete pass on the rollout buffer
            
            for rollout_data in self.rollout_buffer.get(self.batch_size):
                actions = rollout_data.actions
                if isinstance(self.action_space, spaces.Discrete):
                    # Convert discrete action from float to long
                    actions = rollout_data.actions.long().flatten()

                # Re-sample the noise matrix because the log_std has changed
                # TODO: investigate why there is no issue with the gradient
                # if that line is commented (as in SAC)
                if self.use_sde:
                    self.policy.reset_noise(self.batch_size)

                values, log_prob, entropy = self.policy.evaluate_actions(
                    rollout_data.observations, actions
                )
                values = values.flatten()
                # Normalize advantage
                advantages = rollout_data.advantages
                advantages = (advantages - advantages.mean()) / (
                    advantages.std() + 1e-8
                )

                # ratio between old and new policy, should be one at the first iteration
                ratio = th.exp(log_prob - rollout_data.old_log_prob)

                # clipped surrogate loss
                policy_loss_1 = advantages * ratio
                policy_loss_2 = advantages * th.clamp(
                    ratio, 1 - clip_range, 1 + clip_range
                )
                policy_loss = -th.min(policy_loss_1, policy_loss_2).mean()

                # Logging
                pg_losses.append(policy_loss.item())
                clip_fraction = th.mean((th.abs(ratio - 1) > clip_range).float()).item()
                clip_fractions.append(clip_fraction)

                if self.clip_range_vf is None:
                    # No clipping
                    values_pred = values
                else:
                    # Clip the different between old and new value
                    # NOTE: this depends on the reward scaling
                    values_pred = rollout_data.old_values + th.clamp(
                        values - rollout_data.old_values, -clip_range_vf, clip_range_vf
                    )
                # Value loss using the TD(gae_lambda) target
                value_loss = F.mse_loss(rollout_data.returns, values_pred)
                value_losses.append(value_loss.item())
                
                # BC loss
                BC_loss = F.mse_loss(rollout_data.privileged_actions, rollout_data.actions)
                BC_losses.append(BC_loss.item())

                # Entropy loss favor exploration
                if entropy is None:
                    # Approximate entropy when no analytical form
                    entropy_loss = -th.mean(-log_prob)
                else:
                    entropy_loss = -th.mean(entropy)

                entropy_losses.append(entropy_loss.item())

                loss = (
                    policy_loss
                    + self.ent_coef * entropy_loss
                    + self.vf_coef * value_loss
                    + max(self.min_BC_coef,self.BC_scale*self.BC_alpha**self._n_updates)* BC_loss # add BC loss
                )

                # Optimization step
                self.policy.optimizer.zero_grad()
                loss.backward()
                # Clip grad norm
                th.nn.utils.clip_grad_norm_(
                    self.policy.parameters(), self.max_grad_norm
                )
                self.policy.optimizer.step()
                approx_kl_divs.append(
                    th.mean(rollout_data.old_log_prob - log_prob).detach().cpu().numpy()
                )

            all_kl_divs.append(np.mean(approx_kl_divs))

            if (
                self.target_kl is not None
                and np.mean(approx_kl_divs) > 1.5 * self.target_kl
            ):
                print(
                    f"Early stopping at step {epoch} due to reaching max kl: {np.mean(approx_kl_divs):.2f}"
                )
                break

        explained_var = explained_variance(
            self.rollout_buffer.values.flatten(), self.rollout_buffer.returns.flatten()
        )

        # Logs
        self.logger.record("train/entropy_loss", np.mean(entropy_losses))
        wandb.log({'train/entropy_loss':np.mean(entropy_losses)},step = self.num_timesteps)
        self.logger.record("train/policy_gradient_loss", np.mean(pg_losses))
        wandb.log({'train/policy_gradient_loss':np.mean(pg_losses)},step = self.num_timesteps)
        self.logger.record("train/value_loss", np.mean(value_losses))
        wandb.log({'train/value_loss':np.mean(value_losses)},step = self.num_timesteps)
        self.logger.record("train/BC_loss", np.mean(BC_losses))
        wandb.log({'train/BC_loss':np.mean(BC_losses)},step = self.num_timesteps)
        self.logger.record("train/BC_coeff", 
        wandb.log({'train/BC_coeff': max(self.min_BC_coef,self.BC_scale*self.BC_alpha**self._n_updates),},step = self.num_timesteps))
        self.logger.record("train/approx_kl", np.mean(approx_kl_divs))
        wandb.log({'train/approx_kl':np.mean(approx_kl_divs)},step = self.num_timesteps)
        self.logger.record("train/clip_fraction", np.mean(clip_fractions))
        wandb.log({'train/clip_fraction':np.mean(clip_fractions)},step = self.num_timesteps)
        self.logger.record("train/loss", loss.item())
        wandb.log({'train/loss': loss.item()},step = self.num_timesteps)
        self.logger.record("train/explained_variance", explained_var)
        if hasattr(self.policy, "log_std"):
            self.logger.record("train/std", th.exp(self.policy.log_std).mean().item())
            wandb.log({'train/std': th.exp(self.policy.log_std).mean().item()},step = self.num_timesteps)
        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        wandb.log({'train/n_updates': self._n_updates},step = self.num_timesteps)
        self.logger.record("train/clip_range", clip_range)
        wandb.log({'train/clip_range': clip_range},step = self.num_timesteps)
        if self.clip_range_vf is not None:
            self.logger.record("train/clip_range_vf", clip_range_vf)
            wandb.log({'train/clip_range_vf': clip_range_vf},step = self.num_timesteps)
        
        self._n_updates += self.n_epochs
        
    def learn(
        self,
        total_timesteps: int,
        callback: MaybeCallback = None,
        log_interval: int = 1,
        eval_env: Optional[GymEnv] = None,
        eval_freq: int = -1,
        n_eval_episodes: int = 5,
        tb_log_name: str = "PPO",
        eval_log_path: Optional[str] = None,
        reset_num_timesteps: bool = True,
        env_cfg: str = None,
    ) -> "PPO":

        return super(PPO, self).learn(
            total_timesteps=total_timesteps,
            callback=callback,
            log_interval=log_interval,
            eval_env=eval_env,
            eval_freq=eval_freq,
            n_eval_episodes=n_eval_episodes,
            tb_log_name=tb_log_name,
            eval_log_path=eval_log_path,
            reset_num_timesteps=reset_num_timesteps,
            env_cfg=env_cfg,
        )


    def eval(self, iteration=888) -> None:
        save_path = self.logger.get_dir() + "/TestTraj"
        os.makedirs(save_path, exist_ok=True)

        #
        self.policy.eval()
        # self.eval_env.setTimestep(self.num_timesteps)
        self.eval_env.load_rms(
            self.logger.get_dir() + "/RMS/iter_{0:05d}.npz".format(iteration)
        )

        # rollout trajectory and save the trajectory
        traj_df = traj_rollout(self.eval_env, self.policy)
        traj_df.to_csv(save_path + "/test_traj_{0:05d}.csv".format(iteration))

  # generate plots
        fig1 = plt.figure(figsize=(10, 8))
        # fig1.subplots_adjust(
        #     left=None, bottom=None, right=None, top=None, wspace=None, hspace=None
        # )
        gs1 = gridspec.GridSpec(5, 4)
        ax3d = fig1.add_subplot(gs1[3:4, 0:3], projection="3d")
        axpos, axvel, axact = [], [], []
        for i in range(3):
            axpos.append(fig1.add_subplot(gs1[0, i]))
            axvel.append(fig1.add_subplot(gs1[1, i]))
        for i in range(4):
            axact.append(fig1.add_subplot(gs1[2, i]))
        episode_idx = traj_df.episode_id.unique()
        for ep_i in episode_idx:
            conditions = "episode_id == {0}".format(ep_i)
            traj_episode_i = traj_df.query(conditions)
            pos = traj_episode_i.loc[:, ["px", "py", "pz"]].to_numpy(dtype=np.float64)
            vel = traj_episode_i.loc[:, ["vx", "vy", "vz"]].to_numpy(dtype=np.float64)
            act = traj_episode_i.loc[:, ["act1", "act2", "act3","act4"]].to_numpy(dtype=np.float64)
            #
            axpos[0].plot(pos[:, 0])
            axpos[1].plot(pos[:, 1])
            axpos[2].plot(pos[:, 2])
            #
            axvel[0].plot(vel[:, 0])
            axvel[1].plot(vel[:, 1])
            axvel[2].plot(vel[:, 2])
            #
            axact[0].plot(act[:,0])
            axact[1].plot(act[:,1])
            axact[2].plot(act[:,2])
            axact[3].plot(act[:,3])
            #
            plot3d_traj(ax3d=ax3d, pos=pos, vel=vel)
        #
        save_path = self.logger.get_dir() + "/TestTraj" + "/Plots"
        os.makedirs(save_path, exist_ok=True)
        fig1.savefig(save_path + "/traj_3d_{0:05d}.png".format(iteration))