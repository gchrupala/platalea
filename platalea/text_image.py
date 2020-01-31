from collections import Counter
import logging
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from platalea.basic import cyclic_scheduler
import platalea.dataset as D
from platalea.encoders import TextEncoder, ImageEncoder
import platalea.loss
import platalea.score


class TextImage(nn.Module):
    def __init__(self, config):
        super(TextImage, self).__init__()
        self.config = config
        # Components can be pre-instantiated or configured through a dictionary
        if isinstance(config['TextEncoder'], nn.Module):
            self.TextEncoder = config['TextEncoder']
        else:
            self.TextEncoder = TextEncoder(config['TextEncoder'])
        if isinstance(config['ImageEncoder'], nn.Module):
            self.ImageEncoder = config['ImageEncoder']
        else:
            self.ImageEncoder = ImageEncoder(config['ImageEncoder'])

    def cost(self, item):
        text_enc = self.TextEncoder(item['text'], item['text_len'])
        image_enc = self.ImageEncoder(item['image'])
        scores = platalea.loss.cosine_matrix(text_enc, image_enc)
        loss = platalea.loss.contrastive(scores,
                                         margin=self.config['margin_size'])
        return loss

    def embed_image(self, images):
        image = torch.utils.data.DataLoader(dataset=images, batch_size=32,
                                            shuffle=False,
                                            collate_fn=D.batch_image)
        image_e = []
        for i in image:
            image_e.append(self.ImageEncoder(i.cuda()).detach().cpu().numpy())
        image_e = np.concatenate(image_e)
        return image_e

    def embed_text(self, texts):
        texts = [D.Flickr8KData.caption2tensor(t) for t in texts]
        text = torch.utils.data.DataLoader(dataset=texts, batch_size=32,
                                           shuffle=False,
                                           collate_fn=D.batch_text)
        text_e = []
        for t, l in text:
            text_e.append(self.TextEncoder(t.cuda(),
                                           l.cuda()).detach().cpu().numpy())
        text_e = np.concatenate(text_e)
        return text_e


def experiment(net, data, config):
    def val_loss():
        net.eval()
        result = []
        for item in data['val']:
            item = {key: value.cuda() for key, value in item.items()}
            result.append(net.cost(item).item())
        net.train()
        return torch.tensor(result).mean()

    net.cuda()
    net.train()
    optimizer = optim.Adam(net.parameters(), lr=1)
    scheduler = cyclic_scheduler(optimizer, len(data['train']),
                                 max_lr=config['max_lr'], min_lr=1e-6)
    optimizer.zero_grad()

    with open("result.json", "w") as out:
        for epoch in range(1, config['epochs']+1):
            cost = Counter()
            for j, item in enumerate(data['train'], start=1):
                item = {key: value.cuda() for key, value in item.items()}
                loss = net.cost(item)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                scheduler.step()
                cost += Counter({'cost': loss.item(), 'N': 1})
                if j % 100 == 0:
                    logging.info("train {} {} {}".format(
                        epoch, j, cost['cost']/cost['N']))
                if j % 400 == 0:
                    logging.info("valid {} {} {}".format(epoch, j, val_loss()))
            result = platalea.score.score_text_image(net, data['val'].dataset)
            result['epoch'] = epoch
            print(result, file=out, flush=True)
            logging.info("Saving model in net.{}.pt".format(epoch))
            torch.save(net, "net.{}.pt".format(epoch))
