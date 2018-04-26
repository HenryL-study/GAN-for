#-*- coding:utf-8 -*-
from __future__ import print_function
import tensorflow as tf
# from Conv_lstm_cell import ConvLSTMCell
from tensorflow.python.layers.core import Dense
from CustomGreedyEmbeddingHelper import CustomGreedyEmbeddingHelper
from Custombeam_search_decoder import CustomBeamSearchDecoder

class Seq2seq_Model(object):

    def __init__(self, num_emb, batch_size, emb_dim, encoder_num_units, emb_data,
                 ques_length, ans_length, start_token, gen_filter_sizes, gen_num_filters,
                 learning_rate=0.01, reward_gamma=0.95):
        self.num_emb = num_emb
        self.batch_size = batch_size
        self.emb_dim = emb_dim
        self.emb_data = emb_data
        self.encoder_num_units = encoder_num_units
        self.max_ques_length = ques_length
        self.max_ans_length = ans_length
        self.start_token = tf.constant([start_token] * self.batch_size, dtype=tf.int32)
        self.learning_rate = tf.Variable(float(learning_rate), trainable=False)
        self.reward_gamma = reward_gamma
        self.gen_filter_sizes = gen_filter_sizes
        self.gen_num_filters = gen_num_filters
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

        # self.given_num = tf.placeholder(tf.int32, shape=())

        #with tf.variable_scope('generator'):
        self.g_embeddings = tf.Variable(self.init_matrix([self.num_emb, self.emb_dim]))
            #self.g_params.append(self.g_embeddings)
            #self.g_recurrent_unit = self.create_recurrent_unit(self.g_params)  # maps h_tm1 to h_t for generator
            #self.g_output_unit = self.create_output_unit(self.g_params)  # maps h_t to o_t (output token logits)
        
        self.x = tf.placeholder(tf.int32, shape=[self.batch_size, self.max_ques_length]) # sequence of tokens generated by generator
        self.response = tf.placeholder(tf.float32, shape=[self.batch_size, self.max_ans_length]) # get from rollout policy and discriminator
        self.target_sequence_length = tf.placeholder(tf.int32, [self.batch_size], name='target_sequence_length')
        self.target_response_length = tf.placeholder(tf.int32, [self.batch_size], name='target_response_length')
        self.max_response_length_per_batch = tf.placeholder(tf.int32, shape=())

        with tf.device("/cpu:0"):
            #self.processed_x = tf.transpose(tf.nn.embedding_lookup(self.g_embeddings, self.x), perm=[1, 0, 2])  # seq_length x batch_size x emb_dim
            self.processed_x = tf.nn.embedding_lookup(self.g_embeddings, self.x)
            self.processed_response = tf.nn.embedding_lookup(self.g_embeddings, self.response)
            print("processed_x shape: ", self.processed_x.shape)
            print("processed_response shape: ", self.processed_response.shape)
        
        encoder_output, encoder_state = self.get_encoder_layer(self.processed_x, self.encode_rnn_size, self.encode_layer_size, self.target_sequence_length) #sourse seqlenth

        training_decoder_output, predicting_decoder_output = self.decoding_layer(
            self.decode_layer_size, 
            self.decode_rnn_size,
            self.target_response_length,
            self.max_ans_length,
            encoder_state,
            encoder_output, 
            self.x)
        
        #######################################################################################################
        #  Pre-Training
        #######################################################################################################
        self.g_pretrain_predictions = training_decoder_output.rnn_output
        self.g_pretrain_sample = training_decoder_output.sample_id
        print("self.g_pretrain_predictions: ", self.g_pretrain_predictions)
        masks = tf.sequence_mask(self.target_sequence_length, self.max_response_length_per_batch, dtype=tf.float32, name='masks')
        self.pretrain_loss = tf.contrib.seq2seq.sequence_loss(
            self.g_pretrain_predictions,
            self.response[:,0:self.max_response_length_per_batch],
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
        self.g_samples = predicting_decoder_output.sample_id
        #pad
        pred_len_to_fill = self.max_ques_length - tf.shape(self.g_predictions)[1]
        len_to_fill = self.max_ques_length - tf.shape(self.g_samples)[1]
        #print("len_to_fill: ", len_to_fill)
        paddings = [[0,0],[0,pred_len_to_fill],[0,0]]
        self.g_predictions = tf.pad(self.g_predictions, paddings)
        self.g_samples = tf.pad(self.g_samples, [[0,0],[0,len_to_fill]])

        # #self.rewards_mask = masks * self.rewards[:,0:self.max_sequence_length_per_batch]

        # self.g_loss = tf.contrib.seq2seq.sequence_loss(
        #     self.g_predictions,
        #     self.x,
        #     self.rewards)
        # # -tf.reduce_sum(
        # #     tf.reduce_sum(
        # #         tf.one_hot(tf.to_int32(tf.reshape(self.x, [-1])), self.num_emb, 1.0, 0.0) * tf.log(
        # #             tf.clip_by_value(tf.reshape(self.g_predictions, [-1, self.num_emb]), 1e-20, 1.0)
        # #         ), 1) * tf.reshape(self.rewards, [-1])
        # # )

        # g_opt = self.g_optimizer(self.learning_rate)
        # g_gradients = g_opt.compute_gradients(self.g_loss)
        # self.g_grad_zip = [(tf.clip_by_value(grad, -5., 5.), var) for grad, var in g_gradients if grad is not None]
        # self.g_updates = g_opt.apply_gradients(self.g_grad_zip)


    def init_matrix(self, shape):
        #return tf.random_normal(shape, stddev=0.1)
        
        #TODO decide start & end
        self.seq_start_token = 2
        self.seq_end_token = 3

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
        filters = 3


        def get_lstm_cell(rnn_size):
            lstm_cell = tf.contrib.rnn.BasicLSTMCell(rnn_size)
            return lstm_cell
        
        f_cell = tf.contrib.rnn.MultiRNNCell([get_lstm_cell(rnn_size) for _ in range(num_layers)])
        b_cell = tf.contrib.rnn.MultiRNNCell([get_lstm_cell(rnn_size) for _ in range(num_layers)])
        # f_cell = ConvLSTMCell(shape, filters, kernel)
        # b_cell = ConvLSTMCell(shape, filters, kernel)


        (encoder_fw_outputs, encoder_bw_outputs),\
        (encoder_fw_final_state, encoder_bw_final_state) = \
                tf.nn.bidirectional_dynamic_rnn(cell_fw=f_cell,
                                                    cell_bw=b_cell,
                                                    inputs=input_data,
                                                    sequence_length=source_sequence_length,
                                                    dtype=tf.float32, time_major=False)

        encoder_output = tf.concat((encoder_fw_outputs, encoder_bw_outputs), 2)
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
        #CNN encoder
        cnn_context = self.getCnnEncoder(self.gen_filter_sizes, self.gen_num_filters)

        # 4. Training decoder
        with tf.variable_scope("decode"):
            # 得到help对象
            train_context = tf.expand_dims(cnn_context, 1)
            train_seq_inputs = tf.concat([decoder_embed_input, tf.tile(train_context, [1,self.max_ques_length,1])], 2)
            training_helper = tf.contrib.seq2seq.TrainingHelper(inputs=train_seq_inputs,
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
            # predicting_helper = CustomGreedyEmbeddingHelper(self.g_embeddings,
            #                                                     start_tokens,
            #                                                     self.seq_end_token,
            #                                                     cnn_context)
            # predicting_decoder = tf.contrib.seq2seq.BasicDecoder(attn_cell,
            #                                                 predicting_helper,
            #                                                 attn_cell.zero_state(dtype=tf.float32, batch_size=self.batch_size),
            #                                                 output_layer)
            decoder_initial_state = tf.contrib.seq2seq.tile_batch(attn_cell.zero_state(dtype=tf.float32, batch_size=self.batch_size), multiplier=10)
            predicting_decoder = CustomBeamSearchDecoder(cell=attn_cell,
                                                            embedding=self.g_embeddings,
                                                            start_tokens=start_tokens,
                                                            end_token=self.seq_end_token,
                                                            initial_state=decoder_initial_state,
                                                            beam_width=10,
                                                            cnn_context = cnn_context,
                                                            output_layer=output_layer,
                                                            length_penalty_weight=0.0)

            predicting_decoder_output, _, _ = tf.contrib.seq2seq.dynamic_decode(predicting_decoder,
                                                                impute_finished=True,
                                                                maximum_iterations=max_target_sequence_length)
        
        return training_decoder_output, predicting_decoder_output
    
    # def get_samples(self, sess, input_x, given_num, input_len):
    #     '''
    #     sample once by the given time step
    #     Args:
    #     input_x: [batch_size, seq_length]
    #     given_num: given tokens use for generate
    #     input_len: [seq_len]*batch_size
    #     '''
    #     feed = {self.x: input_x, self.given_num: given_num, self.target_sequence_length: input_len}
    #     samples = sess.run(self.g_rollout, feed)

    #     return samples


    def generate(self, sess, x, x_len, response, res_len):
        outputs = sess.run(self.g_samples, feed_dict={self.x: x, self.target_sequence_length: x_len, self.response: response, self.target_response_length: res_len, self.max_response_length_per_batch: res_len_max})
        return outputs

    def train_step(self, sess, x, x_len, response, res_len):
        x_len_max = max(x_len)
        #print("x_len_max: ", x_len_max)
        outputs = sess.run([self.pretrain_loss, self.pretrain_updates, self.g_pretrain_sample, self.g_samples], feed_dict={self.x: x, self.target_sequence_length: x_len, self.response: response, self.target_response_length: res_len, self.max_response_length_per_batch: res_len_max})
        return outputs
    
    def train_test_step(self, sess, x, x_len, response, res_len):
        res_len_max = max(res_len)
        #print("x_len_max: ", x_len_max)
        outputs = sess.run(self.pretrain_loss, feed_dict={self.x: x, self.target_sequence_length: x_len, self.response: response, self.target_response_length: res_len, self.max_response_length_per_batch: res_len_max})
        return outputs

    def g_optimizer(self, *args, **kwargs):
        return tf.train.AdamOptimizer(*args, **kwargs)

    #define cnn network function
    # An alternative to tf.nn.rnn_cell._linear function, which has been removed in Tensorfow 1.0.1
    # The highway layer is borrowed from https://github.com/mkroutikov/tf-lstm-char-cnn
    def linear(self, input_, output_size, scope=None):
        '''
        Linear map: output[k] = sum_i(Matrix[k, i] * input_[i] ) + Bias[k]
        Args:
        input_: a tensor or a list of 2D, batch x n, Tensors.
        output_size: int, second dimension of W[i].
        scope: VariableScope for the created subgraph; defaults to "Linear".
    Returns:
        A 2D Tensor with shape [batch x output_size] equal to
        sum_i(input_[i] * W[i]), where W[i]s are newly created matrices.
    Raises:
        ValueError: if some of the arguments has unspecified or wrong shape.
    '''

        shape = input_.get_shape().as_list()
        if len(shape) != 2:
            raise ValueError("Linear is expecting 2D arguments: %s" % str(shape))
        if not shape[1]:
            raise ValueError("Linear expects shape[1] of arguments: %s" % str(shape))
        input_size = shape[1]

        # Now the computation.
        with tf.variable_scope(scope or "SimpleLinear"):
            matrix = tf.get_variable("Matrix", [output_size, input_size], dtype=input_.dtype)
            bias_term = tf.get_variable("Bias", [output_size], dtype=input_.dtype)

        return tf.matmul(input_, tf.transpose(matrix)) + bias_term

    def highway(self, input_, size, num_layers=1, bias=-2.0, f=tf.nn.relu, scope='Highway'):
        """Highway Network (cf. http://arxiv.org/abs/1505.00387).
        t = sigmoid(Wy + b)
        z = t * g(Wy + b) + (1 - t) * y
        where g is nonlinearity, t is transform gate, and (1 - t) is carry gate.
        """

        with tf.variable_scope(scope):
            for idx in range(num_layers):
                g = f(self.linear(input_, size, scope='highway_lin_%d' % idx))

                t = tf.sigmoid(self.linear(input_, size, scope='highway_gate_%d' % idx) + bias)

                output = t * g + (1. - t) * input_
                input_ = output

        return output
    
    def getCnnEncoder(self, filter_sizes, num_filters, l2_reg_lambda=0.2):
        self.embedded_chars_expanded = tf.expand_dims(self.processed_x, -1)
        pooled_outputs = []
        for filter_size, num_filter in zip(filter_sizes, num_filters):
            with tf.name_scope("conv-maxpool-%s" % filter_size):
                # Convolution Layer
                filter_shape = [filter_size, self.emb_dim, 1, num_filter]
                W = tf.Variable(tf.truncated_normal(filter_shape, stddev=0.1), name="W")
                b = tf.Variable(tf.constant(0.1, shape=[num_filter]), name="b")
                conv = tf.nn.conv2d(
                    self.embedded_chars_expanded,
                    W,
                    strides=[1, 1, 1, 1],
                    padding="VALID",
                    name="conv")
                # Apply nonlinearity
                h = tf.nn.relu(tf.nn.bias_add(conv, b), name="relu")
                # Maxpooling over the outputs
                pooled = tf.nn.max_pool(
                    h,
                    ksize=[1, self.max_ques_length - filter_size + 1, 1, 1],
                    strides=[1, 1, 1, 1],
                    padding='VALID',
                    name="pool")
                pooled_outputs.append(pooled)
        # Combine all the pooled features
        num_filters_total = sum(num_filters)
        self.h_pool = tf.concat(pooled_outputs, 3)
        self.h_pool_flat = tf.reshape(self.h_pool, [-1, num_filters_total])

        # Add highway
        with tf.name_scope("highway"):
            self.h_highway = self.highway(self.h_pool_flat, self.h_pool_flat.get_shape()[1], 1, 0)

        # Add dropout
        with tf.name_scope("dropout"):
            self.h_drop = tf.nn.dropout(self.h_highway, 0.75)
        
        with tf.name_scope("cnncontext"):
            W = tf.Variable(tf.truncated_normal([num_filters_total, self.emb_dim], stddev=0.1), name="W")
            b = tf.Variable(tf.constant(0.1, shape=[self.emb_dim]), name="b")
            # l2_loss += tf.nn.l2_loss(W)
            # l2_loss += tf.nn.l2_loss(b)
            cnn_context = tf.nn.xw_plus_b(self.h_drop, W, b, name="scores")
        
        return cnn_context #[batch_size, emb_dim]



    
