#-*- coding:utf-8 -*-
from __future__ import print_function
import tensorflow as tf
from Conv_lstm_cell import ConvLSTMCell
from tensorflow.python.layers.core import Dense

class Generator(object):

    def __init__(self, num_emb, batch_size, emb_dim, encoder_num_units, emb_data,
                 sequence_length, start_token,
                 learning_rate=0.01, reward_gamma=0.95):
        self.num_emb = num_emb
        self.batch_size = batch_size
        self.emb_dim = emb_dim
        self.emb_data = emb_data
        self.encoder_num_units = encoder_num_units
        self.max_sequence_length = sequence_length
        self.start_token = tf.constant([start_token] * self.batch_size, dtype=tf.int32)
        self.learning_rate = tf.Variable(float(learning_rate), trainable=False)
        self.reward_gamma = reward_gamma
        #self.g_params = []
        #self.d_params = []
        #self.temperature = 1.0
        self.grad_clip = 5.0

        self.seq_start_token = None
        self.seq_end_token = None
        self.encode_rnn_size = 50
        self.encode_layer_size = 2
        self.decode_rnn_size = 50
        self.decode_layer_size = 2
        self.atten_depth = 50 #The depth of the query mechanism

        self.given_num = tf.placeholder(tf.int32, shape=())

        #with tf.variable_scope('generator'):
        self.g_embeddings = tf.Variable(self.init_matrix([self.num_emb, self.emb_dim]))
            #self.g_params.append(self.g_embeddings)
            #self.g_recurrent_unit = self.create_recurrent_unit(self.g_params)  # maps h_tm1 to h_t for generator
            #self.g_output_unit = self.create_output_unit(self.g_params)  # maps h_t to o_t (output token logits)
        
        self.x = tf.placeholder(tf.int32, shape=[self.batch_size, self.max_sequence_length]) # sequence of tokens generated by generator
        self.rewards = tf.placeholder(tf.float32, shape=[self.batch_size, self.max_sequence_length]) # get from rollout policy and discriminator
        self.target_sequence_length = tf.placeholder(tf.int32, [self.batch_size], name='target_sequence_length')
        self.max_sequence_length_per_batch = tf.placeholder(tf.int32, shape=())

        with tf.device("/cpu:0"):
            #self.processed_x = tf.transpose(tf.nn.embedding_lookup(self.g_embeddings, self.x), perm=[1, 0, 2])  # seq_length x batch_size x emb_dim
            self.processed_x = tf.nn.embedding_lookup(self.g_embeddings, self.x)
            print("processed_x shape: ", self.processed_x.shape)
        
        encoder_output, encoder_state = self.get_encoder_layer(self.processed_x, self.encode_rnn_size, self.encode_layer_size, self.target_sequence_length) #sourse seqlenth

        training_decoder_output, predicting_decoder_output, rollout_decoder_output = self.decoding_layer(
            self.decode_layer_size, 
            self.decode_rnn_size,
            self.target_sequence_length,
            self.max_sequence_length,
            encoder_state,
            encoder_output, 
            self.x)
        
        #######################################################################################################
        #  Pre-Training
        #######################################################################################################
        self.g_pretrain_predictions = training_decoder_output.rnn_output
        self.g_pretrain_sample = training_decoder_output.sample_id
        print("self.g_pretrain_predictions: ", self.g_pretrain_predictions)
        masks = tf.sequence_mask(self.target_sequence_length, self.max_sequence_length_per_batch, dtype=tf.float32, name='masks')
        self.pretrain_loss = tf.contrib.seq2seq.sequence_loss(
            self.g_pretrain_predictions,
            self.x[:,0:self.max_sequence_length_per_batch],
            masks)
        # training updates
        pretrain_opt = self.g_optimizer(self.learning_rate)

        pre_gradients = pretrain_opt.compute_gradients(self.pretrain_loss)
        self.pretrain_grad_zip = [(tf.clip_by_value(grad, -5., 5.), var) for grad, var in pre_gradients if grad is not None]
        self.pretrain_updates = pretrain_opt.apply_gradients(self.pretrain_grad_zip)

        #######################################################################################################
        #  Unsupervised Training
        #######################################################################################################
        #print("id", predicting_decoder_output.sample_id)
        #print("rnn", predicting_decoder_output.rnn_output)
        self.g_predictions = predicting_decoder_output.rnn_output
        #pad
        # len_to_fill = self.max_sequence_length - tf.shape(self.g_predictions)[1]
        # #print("len_to_fill: ", len_to_fill)
        # paddings = [[0,0],[0,len_to_fill],[0,0]]
        # self.g_predictions = tf.pad(self.g_predictions, paddings)
        self.g_samples = predicting_decoder_output.sample_id
        self.g_rollout = rollout_decoder_output.sample_id
        self.g_loss = -tf.reduce_sum(
            tf.reduce_sum(
                tf.one_hot(tf.to_int32(tf.reshape(self.x, [-1])), self.num_emb, 1.0, 0.0) * tf.log(
                    tf.clip_by_value(tf.reshape(self.g_predictions, [-1, self.num_emb]), 1e-20, 1.0)
                ), 1) * tf.reshape(self.rewards, [-1])
        )

        g_opt = self.g_optimizer(self.learning_rate)
        g_gradients = g_opt.compute_gradients(self.g_loss)
        self.g_grad_zip = [(tf.clip_by_value(grad, -5., 5.), var) for grad, var in g_gradients if grad is not None]
        self.g_updates = g_opt.apply_gradients(self.g_grad_zip)


    def init_matrix(self, shape):
        #return tf.random_normal(shape, stddev=0.1)
        
        #TODO decide start & end
        self.seq_start_token = 1
        self.seq_end_token = 2

        embeddings = tf.get_variable("embeddings", shape=self.emb_data.shape, initializer=tf.constant_initializer(self.emb_data), trainable=True)
        return embeddings

    def get_encoder_layer(self, input_data, rnn_size, num_layers, source_sequence_length):
        
        '''
        Encoder layer

        Args:
        - input_data: a Tensor of shape [batch_size, seq_length, emb_dim]
        - rnn_size: num of hidden states in rnn
        - num_layers: layers of rnn
        - source_sequence_length: actual seq lenth of each data
        '''
        #rnn cell
        #change to bi-rnn
        #
        # 首先构造单个rnn cell
        # encoder_f_cell = LSTMCell(self.hidden_size)
        # encoder_b_cell = LSTMCell(self.hidden_size)
        # (encoder_fw_outputs, encoder_bw_outputs),
        # (encoder_fw_final_state, encoder_bw_final_state) = \
        #         tf.nn.bidirectional_dynamic_rnn(cell_fw=encoder_f_cell,
        #                                             cell_bw=encoder_b_cell,
        #                                             inputs=self.encoder_inputs_embedded,
        #                                             sequence_length=self.encoder_inputs_actual_length,
        #                                             dtype=tf.float32, time_major=True)
        #
        ########################################################################################################
        #Conv LSTM params
        shape = [self.emb_dim]
        kernel = [3]
        channels = 1
        filters = 2


        # def get_lstm_cell(rnn_size):
        #     lstm_cell = tf.contrib.rnn.BasicLSTMCell(rnn_size)
        #     return lstm_cell
        
        # f_cell = tf.contrib.rnn.MultiRNNCell([get_lstm_cell(rnn_size) for _ in range(num_layers)])
        # b_cell = tf.contrib.rnn.MultiRNNCell([get_lstm_cell(rnn_size) for _ in range(num_layers)])
        f_cell = ConvLSTMCell(shape, filters, kernel)
        b_cell = ConvLSTMCell(shape, filters, kernel)


        (encoder_fw_outputs, encoder_bw_outputs),\
        (encoder_fw_final_state, encoder_bw_final_state) = \
                tf.nn.bidirectional_dynamic_rnn(cell_fw=f_cell,
                                                    cell_bw=b_cell,
                                                    inputs=tf.expand_dims(input_data, 3),
                                                    sequence_length=source_sequence_length,
                                                    dtype=tf.float32, time_major=False)

        encoder_output = tf.reshape(tf.concat((encoder_fw_outputs, encoder_bw_outputs), 3), [self.batch_size, self.max_sequence_length, -1])
        print("encoder_outputs: ", encoder_output)

        '''
        Don't need now
        '''
        encoder_state = None
        # encoder_final_state_c = tf.concat(
        #     (encoder_fw_final_state.c, encoder_bw_final_state.c), 1)

        # encoder_final_state_h = tf.concat(
        #     (encoder_fw_final_state.h, encoder_bw_final_state.h), 1)

        # encoder_state = LSTMStateTuple(
        #     c=encoder_final_state_c,
        #     h=encoder_final_state_h
        # )

        return encoder_output, encoder_state

    def decoding_layer(self, num_layers, rnn_size, target_sequence_length, 
                        max_target_sequence_length, encoder_state, encoder_output, decoder_input):
        '''
        构造Decoder层
        
        参数：
        #- target_letter_to_int: target数据的映射表
        #- decoding_embedding_size: embed向量大小
        - num_layers: 堆叠的RNN单元数量
        - rnn_size: RNN单元的隐层结点数量
        - target_sequence_length: target数据序列长度
        - max_target_sequence_length: target数据序列最大长度
        - encoder_state: encoder端编码的状态向量
        - encoder_output: 
        - decoder_input: decoder端输入
        '''
        # 1. Embedding
        target_vocab_size = self.num_emb
        # decoder_embeddings = tf.Variable(tf.random_uniform([target_vocab_size, decoding_embedding_size]))
        decoder_embed_input = tf.nn.embedding_lookup(self.g_embeddings, decoder_input)

        # 2. 构造Decoder中的RNN单元
        def get_decoder_cell(rnn_size):
            decoder_cell = tf.contrib.rnn.LSTMCell(rnn_size, initializer=tf.random_uniform_initializer(-0.1, 0.1, seed=2))
            return decoder_cell
        cell = tf.contrib.rnn.MultiRNNCell([get_decoder_cell(rnn_size) for _ in range(num_layers)])
        #Attention
        #encoder_output = tf.transpose(encoder_output, perm=[1, 0, 2]) #time * batch * outputsize
        memory = encoder_output
        attention_mechanism = tf.contrib.seq2seq.BahdanauAttention(
            num_units=self.atten_depth, memory=memory,
            memory_sequence_length=target_sequence_length)
        attn_cell = tf.contrib.seq2seq.AttentionWrapper(
            cell, attention_mechanism, attention_layer_size=(self.atten_depth + rnn_size))
        
        # 3. Output全连接层
        output_layer = Dense(target_vocab_size,
                            kernel_initializer = tf.truncated_normal_initializer(mean = 0.0, stddev=0.1))

        print("max_target_sequence_length: ", max_target_sequence_length)
        # 4. Training decoder
        with tf.variable_scope("decode"):
            # 得到help对象
            training_helper = tf.contrib.seq2seq.TrainingHelper(inputs=decoder_embed_input,
                                                                sequence_length=target_sequence_length,
                                                                time_major=False)
            # 构造decoder
            training_decoder = tf.contrib.seq2seq.BasicDecoder(attn_cell,
                                                            training_helper,
                                                            attn_cell.zero_state(dtype=tf.float32, batch_size=self.batch_size),
                                                            output_layer) 
            training_decoder_output, _, _ = tf.contrib.seq2seq.dynamic_decode(training_decoder,
                                                                        impute_finished=True,
                                                                        maximum_iterations=max_target_sequence_length)
        # 5. Predicting decoder
        # 与training共享参数
        with tf.variable_scope("decode", reuse=True):
            # 创建一个常量tensor并复制为batch_size的大小
            start_tokens = tf.tile(tf.constant([self.seq_start_token], dtype=tf.int32), [self.batch_size], 
                                name='start_tokens')
            predicting_helper = tf.contrib.seq2seq.GreedyEmbeddingHelper(self.g_embeddings,
                                                                    start_tokens,
                                                                    self.seq_end_token)
            predicting_decoder = tf.contrib.seq2seq.BasicDecoder(attn_cell,
                                                            predicting_helper,
                                                            attn_cell.zero_state(dtype=tf.float32, batch_size=self.batch_size),
                                                            output_layer)
            predicting_decoder_output, _, _ = tf.contrib.seq2seq.dynamic_decode(predicting_decoder,
                                                                impute_finished=True,
                                                                maximum_iterations=max_target_sequence_length)

        # 6. reward sample decoder
        # 与training共享参数
        # Need finished !!!
        with tf.variable_scope("decode", reuse=True):
            # 创建一个常量tensor并复制为batch_size的大小
            start_tokens = tf.tile(tf.constant([self.seq_start_token], dtype=tf.int32), [self.batch_size], 
                                name='start_tokens')
            start_tokens_embed = tf.nn.embedding_lookup(self.g_embeddings, start_tokens)
            pad_step_embedded = tf.zeros([self.batch_size, self.emb_dim], dtype=tf.float32)

            def initial_fn():
                initial_elements_finished = (0 >= target_sequence_length)  # all False at the initial step
                initial_input = start_tokens_embed
                return initial_elements_finished, initial_input

            def sample_fn(time, outputs, state):
                # 选择logit最大的下标作为sample
                print("outputs", outputs)
                # output_logits = tf.add(tf.matmul(outputs, self.slot_W), self.slot_b)
                # print("slot output_logits: ", output_logits)
                # prediction_id = tf.argmax(output_logits, axis=1)
                prediction_id = tf.to_int32(tf.argmax(outputs, axis=1))
                return prediction_id

            def next_inputs_fn(time, outputs, state, sample_ids):
                # 输入是h_i+o_{i-1}+c_i
                # time is a tensor finish compare
                print("time: ", time) 
                def f1():
                    pred_embedding = tf.nn.embedding_lookup(self.g_embeddings, sample_ids)
                    next_input = pred_embedding
                    return next_input

                def f2():
                    pred_embedding = self.processed_x[:,time,:]
                    next_input = pred_embedding
                    return next_input

                next_input = tf.cond(tf.less(self.given_num, time), f2, f1)
  
                elements_finished = (time >= target_sequence_length)  # this operation produces boolean tensor of [batch_size]
                all_finished = tf.reduce_all(elements_finished)  # -> boolean scalar
                next_inputs = tf.cond(all_finished, lambda: pad_step_embedded, lambda: next_input)
                next_state = state
                return elements_finished, next_inputs, next_state

            rollout_helper = tf.contrib.seq2seq.CustomHelper(initial_fn, sample_fn, next_inputs_fn)

            rollout_decoder = tf.contrib.seq2seq.BasicDecoder(attn_cell,
                                                            rollout_helper,
                                                            attn_cell.zero_state(dtype=tf.float32, batch_size=self.batch_size),
                                                            output_layer)
            rollout_decoder_output, _, _ = tf.contrib.seq2seq.dynamic_decode(rollout_decoder,
                                                                impute_finished=True,
                                                                maximum_iterations=max_target_sequence_length)
        
        return training_decoder_output, predicting_decoder_output, rollout_decoder_output
    
    def get_samples(self, sess, input_x, given_num, input_len):
        '''
        sample once by the given time step
        Args:
        input_x: [batch_size, seq_length]
        given_num: given tokens use for generate
        input_len: [seq_len]*batch_size
        '''
        feed = {self.x: input_x, self.given_num: given_num, self.target_sequence_length: input_len}
        samples = sess.run(self.g_rollout, feed)

        return samples


    def generate(self, sess, x, x_len):
        outputs = sess.run(self.g_samples, feed_dict={self.x: x, self.target_sequence_length: x_len})
        return outputs

    def pretrain_step(self, sess, x, x_len):
        x_len_max = max(x_len)
        #print("x_len_max: ", x_len_max)
        outputs = sess.run([self.pretrain_loss, self.pretrain_updates, self.g_pretrain_sample], feed_dict={self.x: x, self.target_sequence_length: x_len, self.max_sequence_length_per_batch: x_len_max})
        return outputs

    def g_optimizer(self, *args, **kwargs):
        return tf.train.AdamOptimizer(*args, **kwargs)

    