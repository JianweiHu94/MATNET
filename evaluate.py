import tensorflow as tf
import numpy as np
import argparse
import socket
import importlib
import os
import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'models'))
sys.path.append(os.path.join(BASE_DIR, 'utils'))
import provider
import edge_util
import config
parser = argparse.ArgumentParser()
parser.add_argument('--gpu', type=int, default=0, help='GPU to use [default: GPU 0]')
parser.add_argument('--model', default='matnet', help='Model name: pointnet_cls or pointnet_cls_basic [default: pointnet_cls]')
parser.add_argument('--batch_size', type=int, default=5, help='Batch Size during training [default: 1]')
parser.add_argument('--num_point', type=int, default=config.point_num, help='Point Number [256/512/1024/2048] [default: 1024]')
parser.add_argument('--model_path', default='log/model.ckpt', help='model checkpoint file path [default: log/model.ckpt]')
parser.add_argument('--dump_dir', default='dump', help='dump folder path [dump]')
parser.add_argument('--visu', action='store_true', help='Whether to dump image for error case [default: False]')
FLAGS = parser.parse_args()


BATCH_SIZE = FLAGS.batch_size
NUM_POINT = FLAGS.num_point
MODEL_PATH = FLAGS.model_path
GPU_INDEX = FLAGS.gpu
MODEL = importlib.import_module(FLAGS.model) # import network module
DUMP_DIR = FLAGS.dump_dir
if not os.path.exists(DUMP_DIR): os.mkdir(DUMP_DIR)
LOG_FOUT = open(os.path.join(DUMP_DIR, 'log_evaluate.txt'), 'w')
LOG_FOUT.write(str(FLAGS)+'\n')
NEIGHBOR_NUM = config.neighbor_num
NUM_CLASSES = 40
SHAPE_NAMES = [line.rstrip() for line in \
    open(os.path.join(BASE_DIR, 'data/shape_names.txt'))]

HOSTNAME = socket.gethostname()
fout = open(os.path.join(DUMP_DIR, 'pred_label.txt'), 'w')
# ModelNet40 official train/test split

TEST_FILES = provider.getDataFiles(\
    os.path.join(BASE_DIR, 'data/test_files.txt'))
TEST_FILES_MASK = provider.getDataFiles(\
    os.path.join(BASE_DIR, 'data/test_files_mask.txt'))
TEST_FILES_INDEX = provider.getDataFiles(os.path.join(BASE_DIR, 'data/test_files_index.txt'))

def log_string(out_str):
    LOG_FOUT.write(out_str),
    LOG_FOUT.flush()
    print(out_str)

def evaluate(num_votes):
    is_training = False
     
    with tf.device('/gpu:'+str(GPU_INDEX)):
        pointclouds_pl, labels_pl,index_pl, mask_pl = MODEL.placeholder_inputs(BATCH_SIZE, NUM_POINT, NEIGHBOR_NUM)
        is_training_pl = tf.placeholder(tf.bool, shape=())

        # simple model
        pred, end_points = MODEL.get_model_mask(pointclouds_pl, index_pl, mask_pl, is_training_pl,)
        loss = MODEL.get_loss(pred, labels_pl, end_points)
        
        # Add ops to save and restore all the variables.
        saver = tf.train.Saver()
        
    # Create a session
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    config.log_device_placement = True
    sess = tf.Session(config=config)

    # Restore variables from disk.
    saver.restore(sess, MODEL_PATH)
    log_string("Model restored.")

    ops = {'pointclouds_pl': pointclouds_pl,
           'labels_pl': labels_pl,
           'index_pl': index_pl,
           'mask_pl': mask_pl,
           'is_training_pl': is_training_pl,
           'pred': pred,
           'loss': loss}



    acc = eval_one_epoch(sess, ops)
   
def eval_one_epoch(sess, ops,num_votes=1, topk=1):
    error_cnt = 0
    is_training = False
    total_correct = 0
    total_seen = 0
    loss_sum = 0
    total_seen_class = [0 for _ in range(NUM_CLASSES)]
    total_correct_class = [0 for _ in range(NUM_CLASSES)]
    test_batch_size = BATCH_SIZE

    for fn in range(len(TEST_FILES)):
        log_string('----'+str(fn)+'----')
        current_data, current_label = provider.loadDataFile(TEST_FILES[fn])
        current_mask, current_label = provider.loadDataFile(TEST_FILES_MASK[0])
        current_index, current_label = provider.loadDataFile(TEST_FILES_INDEX[0])
        current_label = np.squeeze(current_label)
        #print(current_data.shape)
        file_size = 0
        if (current_data.shape[0] == current_mask.shape[0] == current_index.shape[0] == current_label.shape[0]) and (
                current_data.shape[1] == current_mask.shape[1] == current_index.shape[1]):
            file_size = current_data.shape[0]
        else:
            print("Test size equal\n")
            exit()

        #file_size = current_data.shape[0]
        num_batches = file_size // BATCH_SIZE
        #print(file_size)
        
        for batch_idx in range(num_batches):
            start_idx = batch_idx * BATCH_SIZE
            end_idx = (batch_idx+1) * BATCH_SIZE
            cur_batch_size = end_idx - start_idx
            
            # Aggregating BEG
            batch_loss_sum = 0 # sum of losses for the batch
            batch_pred_sum = np.zeros((cur_batch_size, NUM_CLASSES)) # score for classes
            batch_pred_classes = np.zeros((cur_batch_size, NUM_CLASSES)) # 0/1 for classes
            for vote_idx in range(num_votes):
                rotated_data = provider.rotate_point_cloud_by_angle(current_data[start_idx:end_idx, :, :],
                                                                    vote_idx / float(num_votes) * np.pi * 2)
                feed_dict = {ops['pointclouds_pl']: rotated_data,
                             ops['labels_pl']: current_label[start_idx:end_idx],
                             ops['mask_pl']: current_mask[start_idx:end_idx, :, :],
                             ops['index_pl']: current_index[start_idx:end_idx, :, :],
                             ops['is_training_pl']: is_training}
                loss_val, pred_val = sess.run([ops['loss'], ops['pred']],
                                          feed_dict=feed_dict)
                batch_pred_sum += pred_val
                batch_pred_val = np.argmax(pred_val, 1)
                for el_idx in range(cur_batch_size):
                    batch_pred_classes[el_idx, batch_pred_val[el_idx]] += 1
                batch_loss_sum += (loss_val * cur_batch_size / float(num_votes))
            #pred_val_topk = np.argsort(batch_pred_sum, axis=-1)[:,-1*np.array(range(topk))-1]
            #pred_val = np.argmax(batch_pred_classes, 1)
            pred_val = np.argmax(batch_pred_sum, 1)
            # Aggregating END
            
            correct = np.sum(pred_val == current_label[start_idx:end_idx])
            #correct = np.sum(pred_val_topk[:,0:topk] == label_val)
            total_correct += correct
            total_seen += cur_batch_size
            loss_sum += batch_loss_sum

            for i in range(start_idx, end_idx):
                l = current_label[i]
                total_seen_class[l] += 1
                total_correct_class[l] += (pred_val[i-start_idx] == l)
                #fout.write('%d %d\n' % (l,pred_val[i-start_idx]))
                
    #print total_seen_class

    
    class_accuracies = np.array(total_correct_class)/np.array(total_seen_class,dtype=np.float)
    for i,name  in enumerate(SHAPE_NAMES):
        log_string('%10s: %.2f'%(name,class_accuracies[i]))
    log_string('eval mean loss: %f' % (loss_sum / float(total_seen)))
    log_string('eval accuracy: %f' % (total_correct / float(total_seen)))
    log_string('eval avg class acc: %f' % (np.mean(np.array(total_correct_class)/np.array(total_seen_class,dtype=np.float))))
    return total_correct / float(total_seen)
    


if __name__=='__main__':
    with tf.Graph().as_default():
        evaluate(num_votes=1)
    LOG_FOUT.close()