#!/usr/bin/env python3


import numpy as np
import os
from data_generator import Generator
from load import get_gnn_inputs
from models import GNN_multiclass
import time
import argparse

import torch
import torch.nn as nn
from torch.nn import init
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F
from losses import compute_loss_multiclass, compute_accuracy_multiclass

parser = argparse.ArgumentParser()

###############################################################################
#                             General Settings                                #
###############################################################################


parser.add_argument('--num_examples_train', nargs='?', const=1, type=int,
                    default=10)
parser.add_argument('--num_examples_test', nargs='?', const=1, type=int,
                    default=1)
parser.add_argument('--edge_density', nargs='?', const=1, type=float,
                    default=0.2)
parser.add_argument('--p_SBM', nargs='?', const=1, type=float,
                    default=0.0)
parser.add_argument('--q_SBM', nargs='?', const=1, type=float,
                    default=0.045)
parser.add_argument('--random_noise', action='store_true')
parser.add_argument('--noise', nargs='?', const=1, type=float, default=0.03)
parser.add_argument('--noise_model', nargs='?', const=1, type=int, default=2)
parser.add_argument('--generative_model', nargs='?', const=1, type=str,
                    default='SBM_multiclass')
parser.add_argument('--batch_size', nargs='?', const=1, type=int, default=1)
parser.add_argument('--mode', nargs='?', const=1, type=str, default='train')
parser.add_argument('--path_gnn', nargs='?', const=1, type=str, default='')
parser.add_argument('--filename_existing_gnn', nargs='?', const=1, type=str, default='')
parser.add_argument('--print_freq', nargs='?', const=1, type=int, default=1)
parser.add_argument('--test_freq', nargs='?', const=1, type=int, default=500)
parser.add_argument('--save_freq', nargs='?', const=1, type=int, default=2000)
parser.add_argument('--clip_grad_norm', nargs='?', const=1, type=float,
                    default=40.0)
parser.add_argument('--freeze_bn', dest='eval_vs_train', action='store_true')
parser.set_defaults(eval_vs_train=False)

###############################################################################
#                                 GNN Settings                                #
###############################################################################

parser.add_argument('--num_features', nargs='?', const=1, type=int,
                    default=10)
parser.add_argument('--num_layers', nargs='?', const=1, type=int,
                    default=1)
parser.add_argument('--n_classes', nargs='?', const=1, type=int,
                    default=2)
parser.add_argument('--J', nargs='?', const=1, type=int, default=2)
parser.add_argument('--N_train', nargs='?', const=1, type=int, default=10)
parser.add_argument('--N_test', nargs='?', const=1, type=int, default=10)
parser.add_argument('--lr', nargs='?', const=1, type=float, default=0.004)

args = parser.parse_args()

if torch.cuda.is_available():
    dtype = torch.cuda.FloatTensor
    dtype_l = torch.cuda.LongTensor
    # torch.cuda.manual_seed(0)
else:
    dtype = torch.FloatTensor
    dtype_l = torch.LongTensor
    # torch.manual_seed(1)

batch_size = args.batch_size
criterion = nn.CrossEntropyLoss()
template1 = '{:<10} {:<10} {:<10} {:<15} {:<10} {:<10} {:<10} '
template2 = '{:<10} {:<10.5f} {:<10.5f} {:<15} {:<10} {:<10} {:<10.3f} \n'
template3 = '{:<10} {:<10} {:<10} '
template4 = '{:<10} {:<10.5f} {:<10.5f} \n'

def train_single(gnn, optimizer, gen, n_classes, it):
    start = time.time()
    W, labels = gen.sample_otf_single(is_training=True, cuda=torch.cuda.is_available())
    labels = labels.type(dtype_l)

    if (args.generative_model == 'SBM_multiclass') and (args.n_classes == 2):
        labels = (labels + 1)/2

    WW, x = get_gnn_inputs(W, args.J)

    if (torch.cuda.is_available()):
        WW.cuda()
        x.cuda()

    pred = gnn(WW.type(dtype), x.type(dtype))

    loss = compute_loss_multiclass(pred, labels, n_classes)
    gnn.zero_grad()
    loss.backward()
    nn.utils.clip_grad_norm_(gnn.parameters(), args.clip_grad_norm)
    optimizer.step()

    acc = compute_accuracy_multiclass(pred, labels, n_classes)

    elapsed = time.time() - start

    if(torch.cuda.is_available()):
        loss_value = float(loss.data.cpu().numpy())
    else:
        loss_value = float(loss.data.numpy())

    info = ['iter', 'avg loss', 'avg acc', 'edge_density',
            'noise', 'model', 'elapsed']
    out = [it, loss_value, acc, args.edge_density,
           args.noise, 'GNN', elapsed]
    print(template1.format(*info))
    print(template2.format(*out))

    del WW
    del x

    return loss_value, acc

def train(gnn, gen, n_classes=args.n_classes, iters=args.num_examples_train):
    gnn.train()
    optimizer = torch.optim.Adamax(gnn.parameters(), lr=args.lr)
    loss_lst = np.zeros([iters])
    acc_lst = np.zeros([iters])
    for it in range(iters):
        loss_single, acc_single = train_single(gnn, optimizer, gen, n_classes, it)
        loss_lst[it] = loss_single
        acc_lst[it] = acc_single
        torch.cuda.empty_cache()
    print ('Avg train loss', np.mean(loss_lst))
    print ('Avg train acc', np.mean(acc_lst))
    print ('Std train acc', np.std(acc_lst))

def test_single(gnn, gen, n_classes, it):

    start = time.time()
    W, labels = gen.sample_otf_single(is_training=False, cuda=torch.cuda.is_available())
    labels = labels.type(dtype_l)
    if (args.generative_model == 'SBM_multiclass') and (args.n_classes == 2):
        labels = (labels + 1)/2
    WW, x = get_gnn_inputs(W, args.J)

    print ('WW', WW.shape)

    if (torch.cuda.is_available()):
        WW.cuda()
        x.cuda()

    pred_single = gnn(WW.type(dtype), x.type(dtype))
    labels_single = labels

    loss_test = compute_loss_multiclass(pred_single, labels_single, n_classes)
    acc_test = compute_accuracy_multiclass(pred_single, labels_single, n_classes)

    elapsed = time.time() - start

    if(torch.cuda.is_available()):
        loss_value = float(loss_test.data.cpu().numpy())
    else:
        loss_value = float(loss_test.data.numpy())

    info = ['iter', 'avg loss', 'avg acc', 'edge_density',
            'noise', 'model', 'elapsed']
    out = [it, loss_value, acc_test, args.edge_density,
           args.noise, 'GNN', elapsed]
    print(template1.format(*info))
    print(template2.format(*out))

    del WW
    del x

    return loss_value, acc_test

def test(gnn, gen, n_classes, iters=args.num_examples_test):
    gnn.train()
    loss_lst = np.zeros([iters])
    acc_lst = np.zeros([iters])
    for it in range(iters):
        loss_single, acc_single = test_single(gnn, gen, n_classes, it)
        loss_lst[it] = loss_single
        acc_lst[it] = acc_single
        torch.cuda.empty_cache()
    print ('Avg test loss', np.mean(loss_lst))
    print ('Avg test acc', np.mean(acc_lst))
    print ('Std test acc', np.std(acc_lst))

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)



if __name__ == '__main__':

    gen = Generator()
    gen.N_train =10
    gen.N_test =10
    gen.edge_density =0.2
    gen.p_SBM =0.0
    gen.q_SBM =0.045
    gen.noise = 0.03
    gen.noise_model =2
    gen.generative_model ='SBM_multiclass'
    gen.n_classes = 2
    torch.backends.cudnn.enabled=False
    gnn = GNN_multiclass(10, 1, 2 + 2, n_classes=2)
    print ('Testing the GNN:')
    test(gnn, gen, 2)
    print ('total num of params:', count_parameters(gnn))




