import os
import argparse
import logging
import time
import json

from util import *
from sampler import WarpSampler, sample_function

from models.cast_1 import CAST1
from models.cast_2 import CAST2
from models.cast_3 import CAST3
from models.cast_4 import CAST4
from models.cast_5 import CAST5
from models.sasrec import SASRec

from tqdm import tqdm
import numpy as np
import tensorflow as tf


os.environ['TF_CPP_MIN_LOG_LEVEL'] = '0'


if __name__ == '__main__':

    MODEL_PATH = os.path.abspath('saved_models')
    logger = logging.getLogger('ir2')
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s",
        handlers=[
            logging.FileHandler("{0}/{1}.log".format('.', 'output')),
            logging.StreamHandler()
        ])

    parser = argparse.ArgumentParser()

    # DATASET PARAMETERS
    parser.add_argument('--dataset', required=True,
                        help='Location of pre-processed dataset')
    parser.add_argument('--limit', default=None, type=int,
                        help='Limit the number of datapoints')
    parser.add_argument('--maxlen', default=50, type=int,
                        help='Maximum length of user item sequence, for zero-padding')

    parser.add_argument('--train_dir', required=True)

    # TRAIN PARAMETERS
    parser.add_argument('--batch_size', default=128,
                        type=int, help='Batch size')
    parser.add_argument('--lr', type=float, default=1e-3, help='Learning rate')
    parser.add_argument('--num_epochs', type=int,
                        default=201, help='Number of epochs')
    parser.add_argument('--max_norm', type=float, default=5.0, help='--')

    # MODEL PARAMETERS
    parser.add_argument('--hidden_units', default=50, type=int)
    parser.add_argument('--num_blocks', default=2, type=int)
    parser.add_argument('--num_heads', default=1, type=int)
    parser.add_argument('--dropout_rate', default=0.5, type=float)
    parser.add_argument('--l2_emb', default=0.0, type=float)
    parser.add_argument('--bin_in_hours', default=24, type=int)
    parser.add_argument('--max_bins', default=200, type=int)

    # MISC.
    parser.add_argument('--saved_model', default='model.pt',
                        type=str, help='File to save model checkpoints')
    parser.add_argument('--test_baseline', default=False, action='store_true')
    parser.add_argument('--seed', default=None, type=int)
    parser.add_argument('--log_scale', default=False, action='store_true')
    parser.add_argument('--input_context', default=False, action='store_true')
    parser.add_argument('--model', default="cast", required=True,
                        help="model to use from {cast, sasrec, castsp}")
    # parser.add_argument('--device', default='cuda', type=str, help='Device to run model on') #TODO: GPU

    args = parser.parse_args()

    # Check if dataset exists
    if not os.path.exists(args.dataset):
        logger.info('Pre-process the data first using the --preprocess flag')
        sys.exit()

    # Check if training directory structure exists
    now = datetime.now()
    TRAIN_FILES_PATH = os.path.join(
        MODEL_PATH, os.path.basename(args.dataset), '{}_{}'.format(args.train_dir, now.strftime("%m-%d-%Y-%H-%M-%S")))
    if not os.path.exists(TRAIN_FILES_PATH):
        os.makedirs(TRAIN_FILES_PATH)

    # Save parameters to file for reproduction
    with open(os.path.join(TRAIN_FILES_PATH, 'params.txt'), 'w') as f:
        json.dump(args.__dict__, f, indent=2)

    # Partition data
    dataset = data_partition(args.dataset, args.log_scale)
    [train, valid, test, usernum, itemnum, ratingnum] = dataset
    print(len(train[1]))
    print(train[1])
    num_batch = round(len(train) / args.batch_size)
    print('usernum', usernum, 'itemnum', itemnum)

    cc = sum([len(v) for v in train.values()])
    logger.info('Average sequence length: {:.2f}'.format(cc / len(train)))

    # RESET GRAPH
    if args.seed:
        random.seed(args.seed)
        np.random.seed(args.seed)
        tf.reset_default_graph()
        tf.set_random_seed(args.seed)

    f = open(os.path.join(TRAIN_FILES_PATH, 'log.txt'), 'w')
    config = tf.ConfigProto()
    # config.gpu_options.allow_growth = True
    # config.allow_soft_placement = True

    # SESSION
    sess = tf.Session(config=config)

    # MODEL
    MODELS = ["cast_1","cast_2", "cast_3", "cast_4","cast_5", "sasrec"]
    if args.model.lower() not in MODELS:
        print("provide model from", MODELS)
        sys.exit(0)

    if args.model == "cast_1":
        model = CAST1(usernum, itemnum, ratingnum, args)
    elif args.model == "cast_2":
        model = CAST2(usernum, itemnum, ratingnum, args)
    elif args.model == "cast_3":
        model = CAST3(usernum, itemnum, ratingnum, args)
    elif args.model == "cast_4":
        model = CAST4(usernum, itemnum, ratingnum, args)
    elif args.model == "cast_5":
        model = CAST5(usernum, itemnum, ratingnum, args)
    elif args.model == "sasrec" or args.test_baseline:
        model = SASRec(usernum, itemnum, args)

    # SAMPLER
    print('usernum', usernum, 'itemnum', itemnum)
    sampler = WarpSampler(args, train, usernum, itemnum,
                          sample_func=sample_function,
                          batch_size=args.batch_size, maxlen=args.maxlen, n_workers=1)

    sess.run(tf.global_variables_initializer())

    # Add TensorBoard
    writer = tf.summary.FileWriter(TRAIN_FILES_PATH, sess.graph)

    # Allow saving of model
    MODEL_SAVE_PATH = os.path.join(TRAIN_FILES_PATH, 'model.ckpt')
    saver = tf.train.Saver()
    if os.path.exists(MODEL_SAVE_PATH):
        saver.restore(sess, MODEL_SAVE_PATH)

    T = 0.0
    t0 = time.time()
    try:
        for epoch in range(1, args.num_epochs + 1):
            for step in tqdm(range(num_batch), total=num_batch, ncols=70, leave=False, unit='b'):
                u, seq, pos, neg, timeseq, ratings_seq, hours_seq, days_seq, orig_seq = sampler.next_batch()

                auc, loss, _, summary, activations = sess.run([model.auc, model.loss, model.train_op,
                                                               model.merged, model.activations], 

                                                              {model.u: u, model.input_seq: seq, model.pos: pos,
                                                               model.neg: neg, model.time_seq: timeseq,
                                                               model.hours: hours_seq,
                                                               model.days: days_seq,
                                                               model.is_training: True})
            
            if summary is not None:
                writer.add_summary(summary, epoch)
                writer.flush()

            if epoch % 20 == 0:
                save_path = saver.save(sess, MODEL_SAVE_PATH)
                logger.info('Model saved in path: %s' % save_path)
                logger.info('Evaluating')
                t1 = time.time() - t0
                T += t1
                t_test = evaluate(model, dataset, args, sess)
                t_valid = evaluate_valid(model, dataset, args, sess)
                logger.info('')
                logger.info('epoch:%d, time: %f(s), valid (NDCG@10: %.4f, HR@10: %.4f), test (NDCG@10: %.4f, HR@10: %.4f)' % (
                    epoch, T, t_valid[0], t_valid[1], t_test[0], t_test[1]))

                f.write(str(t_valid) + ' ' + str(t_test) + '\n')
                f.flush()

                summary = tf.Summary()
                summary.value.add(tag='VALID/NDCG@10',
                                  simple_value=float(t_valid[0]))
                summary.value.add(tag='VALID/HR@10',
                                  simple_value=float(t_valid[1]))
                summary.value.add(tag='TEST/NDCG@10',
                                  simple_value=float(t_test[0]))
                summary.value.add(tag='TEST/HR@10',
                                  simple_value=float(t_test[1]))
                writer.add_summary(summary, epoch)
                t0 = time.time()

    except Exception as e:
        sampler.close()
        f.close()
        logger.error(e)
        exit(1)

    f.close()
    sampler.close()
    print("Done")
