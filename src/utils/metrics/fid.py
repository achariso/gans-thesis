import os
import sys
from typing import Optional, Union, Tuple

import torch
import torch.nn as nn
from IPython import get_ipython
from torch import Tensor
# noinspection PyProtectedMember
from torch.utils.data import Dataset, DataLoader
from torchvision.models import inception_v3
from torchvision.transforms import transforms
from tqdm import tqdm
from tqdm.notebook import tqdm as tqdm_nb

from dataset.deep_fashion import ICRBCrossPoseDataset, ICRBDataset
from modules.generators.pgpg import PGPGGenerator
from utils.torch import matrix_sqrt, cov, ToTensorOrPass, invert_transforms
from utils.train import load_model_chkpt


def _frechet_distance(x_mean: Tensor, y_mean: Tensor, x_cov: Tensor, y_cov: Tensor) -> Tensor:
    """
    Method for returning the Fréchet distance between multivariate Gaussians, parameterized by their means and
    covariance matrices.
    :param x_mean: the mean of the first Gaussian, (n_vars)
    :param y_mean: the mean of the second Gaussian, (n_vars)
    :param x_cov: the covariance matrix of the first Gaussian, (n_vars, n_vars)
    :param y_cov: the covariance matrix of the second Gaussian, (n_vars, n_vars)
    :return: a torch.Tensor object containing the Frechet distance of the two multivariate Gaussian distributions
    """
    return torch.norm(x_mean - y_mean) ** 2 + torch.trace(x_cov + y_cov - 2 * matrix_sqrt(x_cov @ y_cov))


class FID(nn.Module):
    """
    FID Class:
    This class is used to compute the Fréchet Inception Distance (FID) between two given image sets.
    """

    # These are the Inception v3 image transforms
    InceptionV3Transforms = transforms.Compose([
        transforms.Resize(299),
        transforms.CenterCrop(299),
        ToTensorOrPass(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    def __init__(self, chkpts_root: str = '/home/achariso/PycharmProjects/gans-thesis/.checkpoints',
                 device: str = 'cpu', n_samples: int = 512, batch_size: int = 8, crop_fc: bool = True):
        """
        FID class constructor.
        :param chkpts_root: absolute path to model checkpoints directory
        :param device: the device type on which to run the Inception model (defaults to 'cpu')
        :param n_samples: the total number of samples used to compute the metric (defaults to 512; the higher this
                          number gets, the more accurate the metric is)
        :param batch_size: the number of samples to precess at each loop
        :param crop_fc: set to True to crop FC layer from Inception v3 network
        """
        super(FID, self).__init__()
        self.inside_colab = 'google.colab' in sys.modules or \
                            'google.colab' in str(get_ipython()) or \
                            'COLAB_GPU' in os.environ
        if self.inside_colab:
            device = 'cuda'
            self.tqdm = tqdm_nb
        else:
            self.tqdm = tqdm

        # Instantiate Inception v3 model
        self.inception = inception_v3(pretrained=False, init_weights=False)
        load_model_chkpt(model=self.inception, model_name='inception_v3', chkpts_root=chkpts_root)
        self.inception \
            .to(device) \
            .eval()
        # Cutoff FC layer from Inception model when we do not want classification, but feature embedding
        if crop_fc:
            self.inception.fc = nn.Identity()
        # Save params in instance
        self.device = device
        self.n_samples = n_samples
        self.batch_size = batch_size

    # noinspection DuplicatedCode
    def get_embeddings(self, dataloader: DataLoader, gen: nn.Module, gen_transforms: transforms.Compose,
                       target_index: Optional[int] = None, condition_indices: Optional[Union[int, tuple]] = None,
                       z_dim: Optional[int] = None) -> Tuple[Tensor, Tensor]:
        """
        Computes ImageNet embeddings of a batch of real and fake images based on Inception v3 classifier.
        :param dataloader: the torch.utils.data.DataLoader instance to access dataset of real images
        :param gen: the Generator network
        :param gen_transforms: the torchvision transforms on which the generator was trained
        :param target_index: index of target (real) output from the arguments that returns dataset::__getitem__() method
        :param condition_indices: indices of images that will be passed to the Generator in order to generate fake
                                  images (for image-to-image translation tasks). If set to None, the generator is fed
                                  with random noise.
        :param z_dim: if $condition_indices$ is None, then this is necessary to produce random noise to feed into the
                      DCGAN-like generator
        :return: a tuple containing one torch.Tensor object of shape (batch_size, n_features) for each of real, fake
                 images
        """
        gen_transforms_inv = invert_transforms(gen_transforms)
        cur_samples = 0
        real_embeddings_list = []
        fake_embeddings_list = []
        for real_samples in self.tqdm(dataloader, total=self.n_samples // self.batch_size):
            if cur_samples >= self.n_samples:
                break

            # Compute real embeddings
            target_output = real_samples[target_index] if target_index is not None else real_samples
            target_output = target_output.to(self.device)
            real_embeddings = self.inception(FID.InceptionV3Transforms(target_output))
            real_embeddings_list.append(real_embeddings.detach().cpu())

            cur_batch_size = len(target_output)

            # Compute fake embeddings
            gen_inputs = [real_samples[_i] for _i in condition_indices] if condition_indices is not None else \
                torch.randn(cur_batch_size, z_dim)
            gen_inputs = [gen_transforms(gen_input).to(self.device) for gen_input in gen_inputs] \
                if condition_indices is not None else gen_inputs.to(self.device)
            fake_output = gen(*gen_inputs)
            if type(fake_output) == tuple or type(fake_output) == list:
                fake_output = fake_output[-1]
            # ATTENTION: In order to pass generator's output through Inception we must re-normalize tensor stats!
            # Generator output images in the range [-1, 1], since it uses a Tanh() activation layer, whereas Inception
            # v3 receives tensors with its custom normalization. Solutions: Invert normalization in gen_transforms and
            # then pass the image through the Inception transforms
            fake_output = gen_transforms_inv(fake_output)
            fake_embeddings = self.inception(FID.InceptionV3Transforms(fake_output))
            fake_embeddings_list.append(fake_embeddings.detach().cpu())

            cur_samples += cur_batch_size

        return torch.cat(real_embeddings_list, dim=0), torch.cat(fake_embeddings_list, dim=0)

    def forward(self, dataset: Dataset, gen: nn.Module, gen_transforms: transforms.Compose,
                target_index: Optional[int] = None, condition_indices: Optional[Union[int, tuple]] = None,
                z_dim: Optional[int] = None) -> Tensor:
        """
        Compute the Fréchet Inception Distance between random $self.n_samples$ images from the given dataset and same
        number of images generated by the given generator network.
        :param dataset: a torch.utils.data.Dataset object to access real images. Attention: no transforms should be
                        applied when __getitem__ is called since the transforms are different on Inception v3
        :param gen: the Generator network
        :param gen_transforms: the torchvision transforms on which the generator was trained
        :param target_index: index of target (real) output from the arguments that returns dataset::__getitem__() method
        :param condition_indices: indices of images that will be passed to the Generator in order to generate fake
                                  images (for image-to-image translation tasks). If set to None, the generator is fed
                                  with random noise.
        :param z_dim: if $condition_indices$ is None, then this is necessary to produce random noise to feed into the
                      DCGAN-like generator
        :return: a scalar torch.Tensor object containing the computed FID value
        """
        dataloader = DataLoader(dataset=dataset, batch_size=self.batch_size, shuffle=True)

        if self.device == 'cuda' and torch.cuda.is_available():
            torch.cuda.empty_cache()

        real_embeddings, fake_embeddings = self.get_embeddings(dataloader, gen, gen_transforms, target_index,
                                                               condition_indices, z_dim)

        # Compute sample means and covariance matrices
        real_embeddings_mean = torch.mean(real_embeddings, dim=0)
        fake_embeddings_mean = torch.mean(fake_embeddings, dim=0)
        real_embeddings_cov = cov(real_embeddings)
        fake_embeddings_cov = cov(fake_embeddings)

        return _frechet_distance(real_embeddings_mean, fake_embeddings_mean,
                                 real_embeddings_cov, fake_embeddings_cov)


if __name__ == '__main__':
    _fid = FID(n_samples=2, batch_size=1)
    _dataset = ICRBCrossPoseDataset(image_transforms=None, pose=True)
    _gen = PGPGGenerator(c_in=6, c_out=3, w_in=128, h_in=128)
    _gen_transforms = ICRBDataset.get_image_transforms(target_shape=128, target_channels=3)
    fid = _fid(_dataset, _gen, gen_transforms=_gen_transforms, target_index=1, condition_indices=(0, 2))
    print(fid)