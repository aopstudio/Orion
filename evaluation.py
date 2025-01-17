import argparse
import logging
import re
from datetime import datetime
import os

import numpy as np
import torch
from nltk import bleu, meteor
from rouge_score.rouge_scorer import RougeScorer
from tqdm import tqdm
from src.distinct_n.distinct_n.metrics import distinct_n_corpus_level as distinct_n

from inductor import BartInductor, CometInductor

FILES = {
    'amie-yago2': 'data/RE-datasets/AMIE-yago2.txt',
    'rules-yago2': 'data/RE-datasets/RuLES-yago2.txt',
    "openrule155": "data/OpenRule155.txt",
    'fewrel': 'data/RE/fewrel-5.txt',
    'semeval': 'data/RE/semeval-5.txt',
    'TREx': 'data/RE/trex-5.txt',
    'nyt10': 'data/RE/nyt10-5.txt',
    'google-re': 'data/RE/google-re-5.txt',
    'wiki80': 'data/RE/wiki80-5.txt',
}


if not os.path.exists('logs/'):
    os.mkdir('logs/')

logging.basicConfig(
    filename='logs/evaluation-{}.log'.format(str(datetime.now())),
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    datefmt='%m/%d/%Y %H:%M:%S',
    level=logging.INFO)
logger = logging.getLogger(__name__)


def print_config(config):
    config = vars(config)
    # 在日志中输出参数设置，日志文件夹是logs
    logger.info("**************** MODEL CONFIGURATION ****************")
    for key in sorted(config.keys()):
        val = config[key]
        keystr = "{}".format(key) + (" " * (25 - len(key)))
        logger.info("{} -->   {}".format(keystr, val))
    logger.info("**************** MODEL CONFIGURATION ****************")

scorer = RougeScorer(['rougeL'], use_stemmer=True)

def rouge(references, hypothesis):
    scores = []
    for reference in references:
        scores.append(
            scorer.score(
                reference, 
                hypothesis)['rougeL'][2]
        )
    
    return max(scores)


class RelationExtractionEvaluator(object):
    # 构造方法
    def __init__(self, args):
        self.args = args
        # 推理器参数是rule
        if self.args.inductor == 'rule':
            # 设置推理器为BartInductor，并传入参数中的其他内容
            self.inductor = BartInductor(
                group_beam=self.args.group_beam,
                continue_pretrain_instance_generator=self.args.mlm_training,
                continue_pretrain_hypo_generator=self.args.bart_training,
                if_then=self.args.if_then,
            )
        # 推理器参数是comet
        elif self.args.inductor == 'comet':
            # 设置推理器为CometInductor
            self.inductor = CometInductor()

    def clean(self, text):
        segments = text.split('<mask>')
        if len(segments) == 3 and segments[2].startswith('.'):
            return '<mask>'.join(segments[:2]) + '<mask>.'
        else:
            return text
    
    def clean_references(self, texts):
        for i, text in enumerate(texts):
            if text.endswith(" ."):
                texts[i] = text.replace(" .", ".")
        
        return texts

    def self_bleu(self, hypothesis):
        bleus = []
        for i in range(len(hypothesis)):
            bleus.append(bleu(
                hypothesis[:i] + hypothesis[i + 1:],
                hypothesis[i],
                weights=(0.5, 0.5)))

        ret = np.mean(bleus)
        return ret
    # 评估任务
    def evaluate(self, task):
        # 不用跟踪反向梯度计算
        with torch.no_grad():
            # 不同的评估标准
            self.metrics = {
                "bleu-4": [],
                "bleu-3": [],
                "bleu-2": [],
                "bleu-1": [],
                "METEOR": [],
                "ROUGE-L": [],
                "self-BLEU-2": [],
            }
            # 根据task参数打开相关的数据文件
            with open(FILES[task], 'r', encoding='utf-8') as file:
                # 读取文件
                data = file.readlines()
                # 设置进度条长度为行数
                with tqdm(total=len(data)) as pbar:
                    # 循环读取行
                    for row in data:
                        # 读一行数据进度条长度加1
                        pbar.update(1)
                        # 移除头尾空字符，以tab为分隔符拆分字符串
                        row = row.strip().split('\t')
                        # 获取不同的元素
                        inputs, head, tail, relations = row[0], row[1], row[2], row[3]
                        inputs = inputs.strip()
                        # 这种情况特指openrule155
                        if relations.startswith('[') and relations.endswith(']'):
                            # 清理数据，将inputs中的<A>和<B>替换成mask（实际上文件中并没有这种情况）
                            inputs = re.sub("<A>|<B>", "<mask>", inputs)
                            # 将relation作为list进行迭代，把每个<A>和<B>替换成mask，并全部转换成小写，赋值给references这个list
                            references = [relation.replace('<A>', '<mask>').replace('<B>', '<mask>').lower().strip() for relation in eval(relations)]
                        else:   # 不是openrule155任务
                            # 将relation作为list进行迭代，把每个<X>和<Y>替换成mask，并全部转换成小写，赋值给references这个list
                            references = [relations.replace('[X]', '<mask>').replace('[Y]', '<mask>').lower().strip()]
                        # 把每个句号前的空格去掉
                        references = self.clean_references(references)
                        # 调用推导器的生成方法生成假说原子 ********重点*********
                        hypothesis = self.inductor.generate(inputs, k=10, topk=10)
                            
                        logger.info("***********Input************")
                        logger.info(inputs)
                        logger.info("*********Hypothesis*********")
                        for i, hypo in enumerate(hypothesis):
                            # 规范假说的格式
                            hypothesis[i] = self.clean(hypo.lower().strip())
                            # 将假说输出到日志
                            logger.info(hypo)

                        logger.info("****************************")
                        logger.info("*********References*********")
                        # 输出用于参考的关系（正确答案）
                        logger.info(references)
                        logger.info("****************************")
                        # 指标填写
                        if len(hypothesis) == 0:
                            for k in self.metrics.keys():
                                if k != 'self-BLEU-2':
                                    self.metrics[k].append(0.)

                        else:
                            for hypo in hypothesis:
                                try:
                                    self.metrics['bleu-4'].append(
                                        bleu(
                                            [reference.split() for reference in references],
                                            hypo.split(),
                                            weights=(0.25, 0.25, 0.25, 0.25)
                                        )
                                    )
                                except Exception:
                                    logger.warning("Skip bleu-4 in example: {}".format(inputs))
                                    pass

                                try:
                                    self.metrics['bleu-3'].append(
                                        bleu(
                                            [reference.split() for reference in references],
                                            hypo.split(),
                                            weights=(1 / 3, ) * 3
                                        )
                                    )
                                except Exception:
                                    logger.warning("Skip bleu-3 in example: {}".format(inputs))
                                    pass

                                try:
                                    self.metrics['bleu-2'].append(
                                        bleu(
                                            [reference.split() for reference in references],
                                            hypo.split(),
                                            weights=(0.5, 0.5)
                                        )           
                                    )
                                except Exception:
                                    logger.warning("Skip bleu-2 in example: {}".format(inputs))
                                    pass

                                try:
                                    self.metrics['bleu-1'].append(
                                        bleu(
                                            [reference.split() for reference in references],
                                            hypo.split(),
                                            weights=(1.0, )
                                        )
                                    )
                                except Exception:
                                    logger.warning("Skip bleu-1 in example: {}".format(inputs))
                                    pass

                                try:
                                    self.metrics['METEOR'].append(
                                        meteor(
                                            references,
                                            hypo,
                                        )
                                    )
                                except:
                                    logger.warning("Skip METEOR in example: {}".format(inputs))
                                    pass
                                    

                                try:
                                    self.metrics['ROUGE-L'].append(
                                        rouge(
                                            references,
                                            hypo,
                                        )
                                    )
                                except:
                                    logger.warning("Skip ROUGE-L in example: {}".format(inputs))
                                    pass
                            try:
                                self.metrics['self-BLEU-2'].append(
                                    self.self_bleu(
                                        hypothesis,
                                    )
                                )
                            except:
                                logger.warning("Skip self-bleu-2 in example: {}.".format(inputs))
                                pass
                        # break
            # 输出结果和指标
            self.print(task, self.metrics)

    def print(self, task, metrics):
        logger.info("Task: {}".format(str(task)))
        for k, v in metrics.items():
            logger.info("{}: {}".format(k, str(np.mean(v))))

        logger.info("*******************************************************")
        logger.info("*******************************************************")
        logger.info("*******************************************************")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # 推理器，可以选rule或者comet
    parser.add_argument("--inductor", type=str, default='rule')
    # 是否使用束搜索
    parser.add_argument("--group_beam", type=bool, default=False)
    # 是否在预测$P(ins|r_p)$ and $P(r_h|ins)$的预训练掩码语言模型（就是README中说要另外下载的模型）的基础上继续训练
    parser.add_argument("--mlm_training", type=bool, default=False)
    parser.add_argument("--bart_training", type=bool, default=False)
    # 是否使用if-then的prompt模板
    parser.add_argument("--if_then", type=bool, default=False)
    # 任务种类，在FILES常量中有定义
    parser.add_argument("--task", type=str, default='openrule155')
    # 解析传入的参数
    args = parser.parse_args()
    # 输出参数配置信息
    print_config(args)
    # 创建评估器
    evaluator = RelationExtractionEvaluator(args)
    # 根据传入的task参数进行相关的评估任务
    evaluator.evaluate(args.task)
