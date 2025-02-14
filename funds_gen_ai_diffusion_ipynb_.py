# -*- coding: utf-8 -*-
"""Funds_Gen_AI_Diffusion.ipynb"

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1_BDrwm2B1HC-MWjysfPC_gWDslcwU-Nn
"""

!pip install pytorch_lightning

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
import torchvision
from torchvision import datasets, transforms
import pytorch_lightning as pl
from pytorch_lightning.callbacks import TQDMProgressBar
from pytorch_lightning.loggers import TensorBoardLogger
import numpy as np
from tqdm import tqdm

class UNet(nn.Module):
    def __init__(self, in_channels, out_channels, features=[64, 128, 256, 512]):
        super(UNet, self).__init__()
        self.encoder = nn.ModuleList()
        self.decoder = nn.ModuleList()
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)

        # Encoder
        for feature in features:
            self.encoder.append(self._block(in_channels, feature))
            in_channels = feature

        # Bottleneck
        # Just use block we define in this class lower to construct a network
        self.bottleneck = self._block(features[-1], features[-1] * 2)

        # Decoder
        for feature in reversed(features):
            self.decoder.append(
                nn.Sequential(
                    nn.ConvTranspose2d(feature * 2, feature, kernel_size=2, stride=2),
                    self._block(feature * 2, feature)
                )
            )

        self.final_conv = nn.Conv2d(features[0], out_channels, kernel_size=1)

    def forward(self, x):
        # Remember? We also have skip connections in UNet
        skip_connections = []

        for layer in self.encoder:
            x = layer(x)
            skip_connections.append(x)
            x = self.pool(x)

        x = self.bottleneck(x)
        skip_connections = skip_connections[::-1]

        for idx in range(len(self.decoder)):
            x = self.decoder[idx][0](x)  # Upsample
            skip_connection = skip_connections[idx]

            if x.shape != skip_connection.shape:
                x = F.pad(x, (0, skip_connection.shape[3] - x.shape[3], 0, skip_connection.shape[2] - x.shape[2]))

            # Need to combine output of previous layer with skipped connection input
            x = torch.cat((skip_connection, x), dim=1)
            x = self.decoder[idx][1](x)  # Apply block

        return self.final_conv(x)

    def _block(self, in_channels, out_channels):
        return nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

class DiffusionModel(pl.LightningModule):
    def __init__(self, unet, timesteps=1000, beta_start=1e-4, beta_end=2e-2):
        super(DiffusionModel, self).__init__()
        self.unet = unet
        self.timesteps = timesteps

        # Look trough the formulas from lecture slides
        self.betas = torch.linspace(beta_start, beta_end, timesteps)
        self.alphas = 1 - self.betas
        self.alphas_cumprod = torch.cumprod(self.alphas, dim=0)
        self.sqrt_alphas_cumprod = torch.sqrt(self.alphas_cumprod)
        self.sqrt_one_minus_alphas_cumprod = torch.sqrt(1. - self.alphas_cumprod)

    def forward(self, x, t):
        #process data with unet to generate noise added
        return self.unet(x)

    def training_step(self, batch, batch_idx):
        imgs, _ = batch
        batch_size = imgs.shape[0]
        t = torch.randint(0, self.timesteps, (batch_size,), device=self.device).long()
        noise = torch.randn_like(imgs).to(self.device) #true noise
        x_t = self.q_sample(imgs, t, noise)    #the noisy image

        # Task 1 (5 points)
        # Get predictions (what do we predict?)
        # Compute loss
        predicted_noise= self.forward(x_t, t)  #estimation of noise added based on the noisy image and t
        loss = nn.MSELoss()(predicted_noise, noise) #loss

        self.log('train_loss', loss)
        return loss

    def q_sample(self, x_0, t, noise):
        device = x_0.device
        self.sqrt_alphas_cumprod = self.sqrt_alphas_cumprod.to(device)
        self.sqrt_one_minus_alphas_cumprod = self.sqrt_one_minus_alphas_cumprod.to(device)
        t = t.to(device)
        return self.sqrt_alphas_cumprod[t][:, None, None, None] * x_0 + self.sqrt_one_minus_alphas_cumprod[t][:, None, None, None] * noise

    def sample(self, image_size, batch_size):
        # Auxilary function for visualizing the denoised images
        with torch.no_grad():
            x = torch.randn((batch_size, 1, image_size, image_size), device=self.device)
            for t in tqdm(reversed(range(self.timesteps)), desc='Sampling', total=self.timesteps):
                predicted_noise = self.unet(x)
                if t > 0:
                    beta_t = self.betas[t].to(self.device)
                    noise = torch.randn_like(x) if t > 1 else torch.zeros_like(x)
                    x = (x - beta_t / (1 - self.alphas_cumprod[t].to(self.device)) * predicted_noise) / torch.sqrt(self.alphas[t].to(self.device)) + torch.sqrt(beta_t) * noise
        return x

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=2e-4)


    def on_epoch_end(self):
        # Log original and denoised images to TensorBoard
        sample_imgs = next(iter(self.trainer.datamodule.val_dataloader()))[0].to(self.device)
        t = torch.randint(0, self.timesteps, (sample_imgs.size(0),), device=self.device).long()
        noise = torch.randn_like(sample_imgs).to(self.device)
        noisy_imgs = self.q_sample(sample_imgs, t, noise)
        denoised_imgs = self.unet(noisy_imgs)

        # Log images to TensorBoard
        grid_original = torchvision.utils.make_grid(sample_imgs, nrow=4, normalize=True)
        grid_noisy = torchvision.utils.make_grid(noisy_imgs, nrow=4, normalize=True)
        grid_denoised = torchvision.utils.make_grid(denoised_imgs, nrow=4, normalize=True)

        self.logger.experiment.add_image('Original Images', grid_original, self.current_epoch)
        self.logger.experiment.add_image('Noisy Images', grid_noisy, self.current_epoch)
        self.logger.experiment.add_image('Denoised Images', grid_denoised, self.current_epoch)

class MNISTDataModule(pl.LightningDataModule):
    def __init__(self, data_dir='./', batch_size=64):
        super().__init__()
        self.data_dir = data_dir
        self.batch_size = batch_size

    def prepare_data(self):
        datasets.MNIST(self.data_dir, train=True, download=True)
        datasets.MNIST(self.data_dir, train=False, download=True)

    def setup(self, stage=None):
        # Task 2 (2 points)
        # Make necessary transformations
        transform = transforms.Compose([transforms.ToTensor(),transforms.Normalize((0.5,), (0.5,))])

        self.mnist_train = datasets.MNIST(self.data_dir, train=True, transform=transform)
        self.mnist_val = datasets.MNIST(self.data_dir, train=False, transform=transform)

    def train_dataloader(self):
        return DataLoader(self.mnist_train, batch_size=self.batch_size, shuffle=True)

    def val_dataloader(self):
        return DataLoader(self.mnist_val, batch_size=self.batch_size)

# Task 3 (5 points)
# Train model

# Create a PyTorch Lightning Trainer
trainer = pl.Trainer(max_epochs=1)

model = DiffusionModel(UNet(in_channels=1, out_channels=1))

data = MNISTDataModule()
data.prepare_data()
data.setup()
data.train_dataloader()
data.val_dataloader()

trainer.fit(model, data)

# Commented out IPython magic to ensure Python compatibility.
# Task 4 (3 points)
# Log to Tensorboard
# %load_ext tensorboard
# %tensorboard --logdir logs/

# Task 5 (extra 5 points)
# Use a different dataset