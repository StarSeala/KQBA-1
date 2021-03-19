'''
ner_helper.py
命名实体识别模型的组件
@Author: chengjian2018
@Reference: Macan (https://github.com/macanv/BERT-BiLSTM-CRF-NER)
'''

import sys
sys.path.append("..") # bug修复：模块的导入从项目根目录开始

import codecs
import os
import pickle
import collections

import tensorflow as tf
from tensorflow.contrib.layers.python.layers import initializers
from bert import tokenization, modeling, optimization # bug修复：模块的导入从项目根目录开始，lstm_crf_layer同理
from ner.lstm_crf_layer import BLSTM_CRF
from ner import tf_metrics

class InputExample(object):
    """A single training/test example for simple sequence classification."""

    def __init__(self, guid=None, text=None, label=None):
        """Constructs a InputExample.
        Args:
          guid: Unique id for the example.
          text: string. The untokenized text of the sequence.
          label: (Optional) string. The label of the example. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.text = text
        self.label = label

class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids, input_mask, segment_ids, label_ids):
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_ids = label_ids
        # self.label_mask = label_mask

class DataProcessor(object):
    """Base class for data converters for sequence classification data sets."""

    def get_train_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the train set."""
        raise NotImplementedError()

    def get_dev_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the dev set."""
        raise NotImplementedError()

    def get_test_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the test set."""
        raise NotImplementedError()

    def get_labels(self):
        """Gets the list of labels for this data set."""
        raise NotImplementedError()

class NERProcesser(DataProcessor):
    """Processor for the MRPC data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_data(os.path.join(data_dir, "train.txt")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_data(os.path.join(data_dir, "dev.txt")), "dev")

    def get_test_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_data(os.path.join(data_dir, "test.txt")), "test")

    def get_labels(self):
        """See base class."""
        return ['[CLS]', '[SEP]', 'B', 'I', 'O']

    def _read_data(self, input_file):
        """Reads a BIO data.
        Args:
            input_file: 输入的文件名
        Outputs:
            lines: 样本的文本序列与BIO标注序列构成的列表的列表
               e.g. [['机 械 设 计 基 础 的 作 者 是 谁 ？', 'B I I I I I O O O O O O O O'], [...], [...]]
        """
        with codecs.open(input_file, 'r', encoding='utf-8') as f:
            lines = []
            words = []
            labels = []
            for line in f:
                contends = line.strip()
                tokens = contends.split(' ')
                if len(tokens) == 2:
                    words.append(tokens[0])
                    labels.append(tokens[-1])
                else:
                    if len(contends) == 0 and len(words) > 0:
                        lines.append([' '.join(labels), ' '.join(words)])
                        words = []
                        labels = []
                        continue
                if contends.startswith("-DOCSTART-"):
                    continue
        return lines

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            guid = "%s-%s" % (set_type, i)
            text = tokenization.convert_to_unicode(line[1])
            label = tokenization.convert_to_unicode(line[0])
            examples.append(InputExample(guid=guid, text=text, label=label))
        return examples

def create_model(bert_config, is_training, input_ids, input_mask,
                 segment_ids, labels, num_labels, use_one_hot_embeddings,
                 dropout_rate=1.0, lstm_size=1, cell='lstm', num_layers=1):
    """创建模型
    Args:
        bert_config: bert配置
        is_training: 是否是训练模式
        input_ids: 输入
        input_mask: 输入mask
        segment_ids: 补齐标志
        labels: 标签
        num_labels: 标签类别
        use_one_hot_embeddings: 和tpu相关
        dropout_rate: dropout
        lstm_size: lstm尺寸
        cell: 单元类型
        num_layers: 层
    Output: rst (loss, logits, trans, pred_ids)
    """
    # 1. BertModel
    model = modeling.BertModel(
        config=bert_config,
        is_training=is_training,
        input_ids=input_ids,
        input_mask=input_mask,
        token_type_ids=segment_ids,
        use_one_hot_embeddings=use_one_hot_embeddings
    )

    # 2. BertModel输出的embedding，维度是[batch_size, seq_length, embedding_dim]
    embedding = model.get_sequence_output()
    max_seq_length = embedding.shape[1].value
    used = tf.sign(tf.abs(input_ids))
    lengths = tf.reduce_sum(used, reduction_indices=1)

    # 3. 外加一层lstm+crf
    blstm_crf = BLSTM_CRF(embedded_chars=embedding, hidden_unit=lstm_size, cell_type=cell, num_layers=num_layers,
                          dropout_rate=dropout_rate, initializers=initializers, num_labels=num_labels,
                          seq_length=max_seq_length, labels=labels, lengths=lengths, is_training=is_training)
    rst = blstm_crf.add_blstm_crf_layer(crf_only=False) # rst = (loss, logits, trans, pred_ids)

    return rst

def model_fn_builder(bert_config, num_labels, init_checkpoint, learning_rate,
                     num_train_steps, num_warmup_steps, args):

    def model_fn(features, labels, mode, params):

        # 1. 提取Features内容
        tf.logging.info("*** Features ***")
        for name in sorted(features.keys()):
            tf.logging.info("  name = %s, shape = %s" % (name, features[name].shape))
        input_ids = features["input_ids"]
        input_mask = features["input_mask"]
        segment_ids = features["segment_ids"]
        label_ids = features["label_ids"]
        print('shape of input_ids', input_ids.shape)

        # 2. create_model
        is_training = (mode == tf.estimator.ModeKeys.TRAIN)
        total_loss, logits, trans, pred_ids = create_model(
            bert_config, is_training, input_ids, input_mask, segment_ids, label_ids,
            num_labels, False, args.dropout_rate, args.lstm_size, args.cell, args.num_layers)

        tvars = tf.trainable_variables()
        if init_checkpoint:
            (assignment_map, initialized_variable_names) = \
                 modeling.get_assignment_map_from_checkpoint(tvars,
                                                             init_checkpoint)
            tf.train.init_from_checkpoint(init_checkpoint, assignment_map)
        tf.logging.info("**** Trainable Variables ****")
        for var in tvars:
            init_string = ""
            if var.name in initialized_variable_names:
                init_string = ", *INIT_FROM_CKPT*"
            tf.logging.info("  name = %s, shape = %s%s", var.name, var.shape,
                            init_string)

        # 3. 返回EstimatorSpec
        output_spec = None

        if mode == tf.estimator.ModeKeys.TRAIN:
            train_op = optimization.create_optimizer(
                 total_loss, learning_rate, num_train_steps, num_warmup_steps, False)

            hook_dict = {} # hook_dict记录损失和步数信息
            hook_dict['loss'] = total_loss
            hook_dict['global_steps'] = tf.train.get_or_create_global_step()
            logging_hook = tf.train.LoggingTensorHook(
                hook_dict, every_n_iter=args.save_summary_steps)

            output_spec = tf.estimator.EstimatorSpec( # 必需参数：loss，train_op
                mode=mode,
                loss=total_loss,
                train_op=train_op,
                training_hooks=[logging_hook])

        elif mode == tf.estimator.ModeKeys.EVAL:
            # PROBLEM REMAIN: eval的评估指标
            def metric_fn(label_ids, pred_ids):

                indices = [2, 3, 4]  # PROBLEM REMAIN: 与NERProcessor下标对应？
                weight = tf.sequence_mask(args.max_seq_length)
                precision = tf_metrics.precision(label_ids, pred_ids, num_labels, indices, weight)
                recall = tf_metrics.recall(label_ids, pred_ids, num_labels, indices, weight)
                f1 = tf_metrics.f1(label_ids, pred_ids, num_labels, indices, weight)

                return {
                    "eval_precision": precision,
                    "eval_recall": recall,
                    "eval_f": f1}

            eval_metrics = metric_fn(label_ids, pred_ids)
            output_spec = tf.estimator.EstimatorSpec( # 必需参数：loss
                mode=mode,
                loss=total_loss,
                eval_metric_ops=eval_metrics)

        else:
            output_spec = tf.estimator.EstimatorSpec( # 必需参数：predictions
                mode=mode,
                predictions=pred_ids)

        return output_spec

    return model_fn

def write_tokens(tokens, output_dir, mode):
    """将序列解析结果写入到文件中
    Args:
        tokens: 解析结果
        output_dir: 输出路径
        mode: 模式，只有当mode为test时写入
    """
    if mode == "test":
        path = os.path.join(output_dir, "token_" + mode + ".txt")
        wf = codecs.open(path, 'a', encoding='utf-8')
        for token in tokens:
            if token != "**NULL**":
                wf.write(token + '\n')
        wf.close()

def convert_single_example(ex_index, example, label_list, max_seq_length, tokenizer, output_dir, mode):
    """将一个example转换为feature
    Args:
        ex_index: 编号
        example: 样本
        label_list: 标签集合
        max_seq_length: 序列长度
        tokenizer: 分词器
        output_dir: 输出文件文件夹
        mode: 模式
    Output:
        feature: example对应的feature
    """

    # 1. 标签映射
    label_map = {}
    for (i, label) in enumerate(label_list, 1): # 从1开始编号
        label_map[label] = i
    if not os.path.exists(os.path.join(output_dir, 'label2id.pkl')): # 将标签映射存入文件
        with codecs.open(os.path.join(output_dir, 'label2id.pkl'), 'wb') as w:
            pickle.dump(label_map, w)

    # 2. 获取token序列和label序列
    textlist = example.text.split(' ') # 原始text列表
    labellist = example.label.split(' ') # 原始label列表
    tokens = [] # 结果token列表
    labels = [] # 结果label列表
    for i, word in enumerate(textlist):
        token = tokenizer.tokenize(word)
        tokens.extend(token)
        label_1 = labellist[i]
        for m in range(len(token)):
            if m == 0:
                labels.append(label_1)
            else:
                labels.append("X")

    # 3. 序列截断/填充
    if len(tokens) >= max_seq_length - 1:
        tokens = tokens[0:(max_seq_length - 2)]  # -2 的原因是因为序列需要加一个句首和句尾标志
        labels = labels[0:(max_seq_length - 2)]
    ntokens = []
    segment_ids = []
    label_ids = []
    ntokens.append("[CLS]")
    segment_ids.append(0)
    label_ids.append(label_map["[CLS]"])
    for i, token in enumerate(tokens):
        ntokens.append(token)
        segment_ids.append(0)
        label_ids.append(label_map[labels[i]])
    ntokens.append("[SEP]")
    segment_ids.append(0)
    label_ids.append(label_map["[SEP]"])
    input_ids = tokenizer.convert_tokens_to_ids(ntokens)
    input_mask = [1] * len(input_ids)
    while len(input_ids) < max_seq_length:
        input_ids.append(0)
        input_mask.append(0)
        segment_ids.append(0)
        label_ids.append(0)
        ntokens.append("**NULL**")

    assert len(input_ids) == max_seq_length
    assert len(input_mask) == max_seq_length
    assert len(segment_ids) == max_seq_length
    assert len(label_ids) == max_seq_length

    # 4. 打印事例
    if ex_index < 5:
        tf.logging.info("*** Example ***")
        tf.logging.info("guid: %s" % (example.guid))
        tf.logging.info("tokens: %s" % " ".join(
            [tokenization.printable_text(x) for x in tokens]))
        tf.logging.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
        tf.logging.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
        tf.logging.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
        tf.logging.info("label_ids: %s" % " ".join([str(x) for x in label_ids]))

    # 5. 构建Feature类
    feature = InputFeatures(
        input_ids=input_ids,
        input_mask=input_mask,
        segment_ids=segment_ids,
        label_ids=label_ids)

    # 6.
    write_tokens(ntokens, output_dir, mode)
    return feature

def filed_based_convert_examples_to_features(
        examples, label_list, max_seq_length, tokenizer, output_file, output_dir, mode=None):
    """将Examples转化为Features，并写入tf_record"""

    # 1. 创建TFRecord的writer
    writer = tf.python_io.TFRecordWriter(output_file)

    # 2. 转换example到feature，写入TFRecord
    for (ex_index, example) in enumerate(examples):
        if ex_index % 5000 == 0:
            tf.logging.info("Writing example %d of %d" % (ex_index, len(examples)))

        feature = convert_single_example(ex_index, example, label_list, max_seq_length, tokenizer, output_dir, mode)

        def create_int_feature(values):
            f = tf.train.Feature(int64_list=tf.train.Int64List(value=list(values)))
            return f

        features = collections.OrderedDict()
        features["input_ids"] = create_int_feature(feature.input_ids)
        features["input_mask"] = create_int_feature(feature.input_mask)
        features["segment_ids"] = create_int_feature(feature.segment_ids)
        features["label_ids"] = create_int_feature(feature.label_ids)

        tf_example = tf.train.Example(features=tf.train.Features(feature=features))
        writer.write(tf_example.SerializeToString())

def file_based_input_fn_builder(input_file, seq_length, is_training, drop_remainder):

    name_to_features = {
        "input_ids": tf.FixedLenFeature([seq_length], tf.int64),
        "input_mask": tf.FixedLenFeature([seq_length], tf.int64),
        "segment_ids": tf.FixedLenFeature([seq_length], tf.int64),
        "label_ids": tf.FixedLenFeature([seq_length], tf.int64),
        # "label_mask": tf.FixedLenFeature([seq_length], tf.int64)
    }

    def _decode_record(record, name_to_features):
        example = tf.parse_single_example(record, name_to_features)
        for name in list(example.keys()):
            t = example[name]
            if t.dtype == tf.int64:
                t = tf.to_int32(t)
            example[name] = t
        return example

    def input_fn(params):
        batch_size = params["batch_size"]
        d = tf.data.TFRecordDataset(input_file)
        if is_training:
            d = d.repeat()
            d = d.shuffle(buffer_size=300)
        d = d.apply(tf.data.experimental.map_and_batch(lambda record: _decode_record(record, name_to_features),
                                                       batch_size=batch_size,
                                                       num_parallel_calls=8,  # 并行处理数据的CPU核心数量，不要大于你机器的核心数
                                                       drop_remainder=drop_remainder))
        d = d.prefetch(buffer_size=4)
        return d

    return input_fn