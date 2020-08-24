import numpy as np
import tensorflow as tf
import gym
from datetime import datetime
from collections import deque
import time
import sys
from atari_wrappers import make_env

def cnn(x):
    x = tf.layers.conv2d(x, filters=16, kernel_size=8, strides=4, padding='valid', activation='relu')
    x = tf.layers.conv2d(x, filters=32, kernel_size=4, strides=2, adding='valid', activation='relu')
    return tf.layers.conv2d(x, filters=32, kernel_size=3, strides=1, padding='valid', activation='relu')
def fnn(x, hidden_layers, output_layer, activation=tf.nn.relu,
    last_activation=None):
    for l in hidden_layers:
        x = tf.layers.dense(x, units=l, activation=activation)
    return tf.layers.dense(x, units=output_layer, activation=last_activation)

def qnet(x, hidden_layers, output_size, fnn_activation=tf.nn.relu, last_activation=None):
    x = cnn(x)
    x = tf.layers.flatten(x)
    return fnn(x, hidden_layers, output_size, fnn_activation, last_activation)

class ExperienceBuffer():
    def __init__(self, buffer_size):
        self.obs_buf = deque(maxlen=buffer_size)
        self.rew_buf = deque(maxlen=buffer_size)
        self.act_buf = deque(maxlen=buffer_size)
        self.obs2_buf = deque(maxlen=buffer_size)
        self.done_buf = deque(maxlen=buffer_size)
    def add(self, obs, rew, act, obs2, done):
        self.obs_buf.append(obs)
        self.rew_buf.append(rew)
        self.act_buf.append(act)
        self.obs2_buf.append(obs2)
        self.done_buf.append(done)

    def sample_minibatch(self, batch_size):
        mb_indices = np.random.randint(len(self.obs_buf), size=batch_size)
        mb_obs = scale_frames([self.obs_buf[i] for i in mb_indices])
        mb_rew = [self.rew_buf[i] for i in mb_indices]
        mb_act = [self.act_buf[i] for i in mb_indices]
        mb_obs2 = scale_frames([self.obs2_buf[i] for i in mb_indices])
        mb_done = [self.done_buf[i] for i in mb_indices]
        return mb_obs, mb_rew, mb_act, mb_obs2, mb_done
        
    def __len__(self):
        return len(self.obs_buf)

def DQN(env_name, hidden_sizes=[32], lr=1e-2, num_epochs=2000,
        buffer_size=100000, discount=0.99, update_target_net=1000, batch_size=64,
        update_freq=4, frames_num=2, min_buffer_size=5000, test_frequency=20,
        start_explor=1, end_explor=0.1, explor_steps=100000):
    env = make_env(env_name, frames_num=frames_num, skip_frames=True, noop_num=20)
    env_test = make_env(env_name, frames_num=frames_num, skip_frames=True, noop_num=20)
    env_test = gym.wrappers.Monitor(env_test,"VIDEOS/TEST_VIDEOS"+env_name+str(current_milli_time()),force=True,video_callable=lambda x: x%20==0)
    obs_dim = env.observation_space.shape
    act_dim = env.action_space.n
    tf.reset_default_graph()
    obs_ph = tf.placeholder(shape=(None, obs_dim[0], obs_dim[1], obs_dim[2]), dtype=tf.float32, name='obs')
    act_ph = tf.placeholder(shape=(None,), dtype=tf.int32, name='act')
    y_ph = tf.placeholder(shape=(None,), dtype=tf.float32, name='y')

    with tf.variable_scope('target_network'):
        target_qv = qnet(obs_ph, hidden_sizes, act_dim)
        target_vars = tf.trainable_variables()
    with tf.variable_scope('online_network'):
        online_qv = qnet(obs_ph, hidden_sizes, act_dim)
        train_vars = tf.trainable_variables()
        update_target = [train_vars[i].assign(train_vars[i+len(target_vars)]) for i in range(len(train_vars) - len(target_vars))]
        update_target_op = tf.group(*update_target)

    act_onehot = tf.one_hot(act_ph, depth=act_dim)
    q_values = tf.reduce_sum(act_onehot * online_qv, axis=1)
    v_loss = tf.reduce_mean((y_ph - q_values)**2)

    now = datetime.now()
    clock_time = "{}_{}.{}.{}".format(now.day, now.hour, now.minute,
    int(now.second))
    mr_v = tf.Variable(0.0)
    ml_v = tf.Variable(0.0)
    tf.summary.scalar('v_loss', v_loss)
    tf.summary.scalar('Q-value', tf.reduce_mean(q_values))
    tf.summary.histogram('Q-values', q_values)
    scalar_summary = tf.summary.merge_all()
    reward_summary = tf.summary.scalar('test_rew', mr_v)
    mean_loss_summary = tf.summary.scalar('mean_loss', ml_v)
    hyp_str = "-lr_{}-upTN_{}-upF_{}-frms_{}".format(lr, update_target_net, update_freq, frames_num)
    file_writer =tf.summary.FileWriter('log_dir/'+env_name+'/DQN_'+clock_time+'_'+hyp_str,tf.get_default_graph())

    def agent_op(o):
        o = scale_frames(o)
        return sess.run(online_qv, feed_dict={obs_ph:[o]})
    
    sess = tf.Session()
    sess.run(tf.global_variables_initializer())
    step_count = 0
    last_update_loss = []
    ep_time = current_milli_time()

    batch_rew = []
    obs = env.reset()  

    obs = env.reset()
    buffer = ExperienceBuffer(buffer_size)
    sess.run(update_target_op)
    eps = start_explor
    eps_decay = (start_explor - end_explor) / explor_steps

    for ep in range(num_epochs):
        g_rew = 0
        done = False
        while not done:
            act = eps_greedy(np.squeeze(agent_op(obs)), eps=eps)
            obs2, rew, done, _ = env.step(act)
            buffer.add(obs, rew, act, obs2, done)
            obs = obs2
            g_rew += rew
            step_count += 1

            if eps > end_explor:
                eps -= eps_decay

            if len(buffer) > min_buffer_size and (step_count % update_freq == 0):
                mb_obs, mb_rew, mb_act, mb_obs2, mb_done = buffer.sample_minibatch(batch_size)
                mb_trg_qv = sess.run(target_qv, feed_dict={obs_ph:mb_obs2})
                y_r = q_target_values(mb_rew, mb_done, mb_trg_qv, discount)
                # Compute the target values
                train_summary, train_loss, _ = sess.run([scalar_summary, v_loss, v_opt], feed_dict={obs_ph:mb_obs, y_ph:y_r, act_ph: mb_act})
                file_writer.add_summary(train_summary, step_count)
                last_update_loss.append(train_loss)

            if (len(buffer) > min_buffer_size) and (step_count % update_target_net) == 0:
                _, train_summary = sess.run([update_target_op, mean_loss_summary], feed_dict={ml_v:np.mean(last_update_loss)})
                file_writer.add_summary(train_summary, step_count)
                last_update_loss = []
            
            if done:
                obs = env.reset()
                batch_rew.append(g_rew)
                g_rew = 0

        if ep % test_frequency == 0:
            test_rw = test_agent(env_test, agent_op, num_games=10)
            test_summary = sess.run(reward_summary, feed_dict={mr_v:np.mean(test_rw)})
            file_writer.add_summary(test_summary, step_count)
            print('Ep:%4d Rew:%4.2f, Eps:%2.2f -- Step:%5d -- Test:%4.2f %4.2f' % (ep,np.mean(batch_rew), eps, step_count, np.mean(test_rw), np.std(test_rw)))
            batch_rew = []
    file_writer.close()
    env.close()
    env_test.close()

if __name__ == '__main__':
    DQN('PongNoFrameskip-v4', hidden_sizes=[128], lr=2e-4, buffer_size=100000, update_target_net=1000, batch_size=32, update_freq=2, frames_num=2, min_buffer_size=10000)