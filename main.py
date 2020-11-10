from collections import defaultdict
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms
import numpy as np
from models import CAE1Layer, CAE2Layer, MTC
from utils import cae_h_loss, MTC_loss, calculate_singular_vectors_B, knn_distances, sigmoid
import argparse
from collections import Counter
torch.manual_seed(42)


parser = argparse.ArgumentParser(description='Implementation of CAE+H',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)


parser.add_argument('--dataset', type=str, default="MNIST")
parser.add_argument('--learning_rate', type=float, default=0.001)
parser.add_argument('--batch_size', type=int, default=100)
parser.add_argument('--lambd', type=float, default=0.0)
parser.add_argument('--gamma', type=float, default=0.01,
                    help='gamma')

parser.add_argument('--code_size', type=int, default=60,
                    help='dimension of hidden layer')
parser.add_argument('--code_size2', type=int, default=None,
                    help='dimension of hidden layer')

parser.add_argument('--epsilon', type=float, default=0.1,
                    help='std for random noise')

parser.add_argument('--epochs', type=int, default=100,
                    help='upper epoch limit')

parser.add_argument('--numlayers', type=int, default=1,
                    help='layers of CAE+H (1 or 2)')

parser.add_argument('--save_dir_for_CAE', type=str, default=None,
                    help='directory for saving weights')

parser.add_argument('--pretrained_CAEH', type=str, default=None,
                    help='path to pretrainded state_dict for CAEH. If provided, we will not train CAEH model')

parser.add_argument('--KNN', type=bool, default=False,
                    help='KNN or not')

parser.add_argument('--train_CAEH', type=bool, default=True,
                    help='train_CAEH or not')

#MTC
parser.add_argument('--MTC', type=bool, default=False,
                    help='train MTC or not')
parser.add_argument('--dM', type=int, default=15,
                    help='number of leading singular vectors')


parser.add_argument('--beta', type=float, default=0.1)
parser.add_argument('--MTC_epochs', type=float, default=100)
parser.add_argument('--MTC_lr', type=float, default=0.001)

args = parser.parse_args()


image_size = 28
dimensionality = image_size*image_size
batch_size = args.batch_size


if args.dataset == "MNIST":
    train_dataset= datasets.MNIST('data', train=True, download=True, transform=transforms.ToTensor())
    test_dataset = datasets.MNIST('data', train=False, download=True, transform=transforms.ToTensor())
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size)
    test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=batch_size)
else:
    raise Exception("Sorry, only MNIST")


epoch_size = len(train_dataset) // batch_size


if args.numlayers==1:
    model = CAE1Layer(dimensionality, args.code_size)
elif args.numlayers==2:
    model = CAE2Layer(dimensionality, [args.code_size, args.code_size2])
else:
    raise Exception("Sorry, numlayers only 1 or 2")

model.cuda()
optimizer = optim.Adam(model.parameters(), lr=args.learning_rate)

if args.pretrained_CAEH:
    model.load_state_dict(torch.load(args.pretrained_CAEH))
elif args.train_CAEH:
    for i in range(args.epochs):
        train_loss = 0
        MSE_loss = 0 
        for step, (imgs, _) in enumerate(train_loader):
            imgs = imgs.view(batch_size, -1).cuda()
            imgs.requires_grad_(True)
            imgs_noise = torch.autograd.Variable(imgs.data + torch.normal(0, args.epsilon, size=[batch_size, dimensionality]).cuda(),requires_grad=True)

            recover, code_data, code_data_noise = model(imgs, imgs_noise)
            loss, loss1 = cae_h_loss(imgs, imgs_noise, recover, code_data,
                              code_data_noise, args.lambd, args.gamma, batch_size)

            imgs.requires_grad_(False)
            imgs_noise.requires_grad_(False)

            loss.backward()

            train_loss += loss.item()
            MSE_loss += loss1.item()
            optimizer.step()

            optimizer.zero_grad()

        if i % 10 == 0:
            print(i, train_loss/epoch_size)


if args.save_dir_for_CAE:
    torch.save(model.state_dict(), args.save_dir_for_CAE)

if args.MTC:
    U=calculate_singular_vectors_B(model, train_loader, args.dM, batch_size)
    number_of_classes = len(train_dataset.classes)
    MTC_model = MTC(model, number_of_classes)
    MTC_model.cuda()
    optimizer = optim.Adam(MTC_model.parameters(), lr = args.MTC_lr)
    for step in range(args.MTC_epochs):
        train_loss = 0
        CE_loss = 0
        for (imgs, y), u in zip(train_loader, U):
            imgs = imgs.view(batch_size, -1).cuda()
            imgs.requires_grad_(True)
            y = y.cuda()
            pred = MTC_model(imgs)
            loss, loss1 = MTC_loss(pred, y, u, imgs, args.beta)
            imgs.requires_grad_(False)
            loss.backward()
            train_loss += loss.item()
            CE_loss += loss1.item()
            optimizer.step()

            optimizer.zero_grad()
    print(step, train_loss/epoch_size, CE_loss/epoch_size)




#if CAEH + KNN
if args.KNN:
    train_dataset = datasets.MNIST('data', train=True, download=True, transform=transforms.ToTensor())
    test_dataset = datasets.MNIST('data', train=False, download=True, transform=transforms.ToTensor())

    test_size=10000
    train_size=1000
    train_dataset=torch.utils.data.Subset(train_dataset,range(0,train_size))
    test_dataset=torch.utils.data.Subset(test_dataset,range(0,test_size))
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=len(train_dataset))
    test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=len(test_dataset))

    train_images = next(iter(train_loader))[0].numpy()
    train_labels = next(iter(train_loader))[1].numpy()
    test_images = next(iter(test_loader))[0].numpy()
    test_labels = next(iter(test_loader))[1].numpy()

    train_images=np.reshape(train_images, (train_size, -1))
    test_images=np.reshape(test_images, (test_size, -1))

    weights=None
    if args.numlayers==1:
        cur_W1 = model.W1.cpu().detach().numpy()
        cur_b1 = model.b1.cpu().detach().numpy()
        weights=[[cur_W1, cur_b1]]
    elif args.numlayers==2:
        cur_W1 = model.W1.cpu().detach().numpy()
        cur_b1 = model.b1.cpu().detach().numpy()
        cur_W2 = model.W2.cpu().detach().numpy()
        cur_b2 = model.b2.cpu().detach().numpy()
        weights=[[cur_W1, cur_b1],[cur_W2, cur_b2]]

    #encode images
    for W,b in weights:
        train_images = sigmoid(np.matmul(train_images, W.T) + b)
        test_images = sigmoid(np.matmul(test_images, W.T) + b)

    # del model
    # torch.cuda.empty_cache()

    # Predicting and printing the accuracy
    
    ks=np.arange(1,20,2)

    i = 0
    total_correct={}
    for k in ks:
        total_correct[k]=0
        
    for test_image in test_images:
        top_n_labels = knn_distances(train_images, train_labels, test_image, n_top=20)
        for k in ks:
            pred = Counter(top_n_labels[:k]).most_common(1)[0][0]
            if pred == test_labels[i]:
                total_correct[k] += 1
        if i%4000 == 0:
            print('test image['+str(i)+']')
        i += 1

    accuracies = {k: round((v/i) * 100, 2) for k,v in total_correct.items()}

    for k in ks:
        with open('results_CAEH.txt','a') as f:
            f.write("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(MSE_loss/epoch_size, args.learning_rate, args.lambd, args.gamma, args.code_size, args.code_size2, args.epsilon, k, accuracies[k]))


