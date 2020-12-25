from typing import Optional, Union

import torch
import torch.nn as nn
import torch.utils.data
from torch import Tensor
# noinspection PyProtectedMember
from torch.utils.data import Dataset, DataLoader
from torchvision.transforms import transforms

from dataset.deep_fashion import ICRBCrossPoseDataset, ICRBDataset
from modules.generators.pgpg import PGPGGenerator
from utils.metrics.fid import FID
from utils.torch import invert_transforms


class IS(FID):
    """
    IS Class:
    This class is used to compute the Inception Score (IS) between two given image sets.
    """

    def __init__(self, chkpts_root: str = '/home/achariso/PycharmProjects/gans-thesis/.checkpoints',
                 n_samples: int = 512, batch_size: int = 8, device: str = 'cpu'):
        """
        IS class constructor.
        :param chkpts_root: absolute path to model checkpoints directory
        :param n_samples: the total number of samples used to compute the metric (defaults to 512; the higher this
                          number gets, the more accurate the metric is)
        :param batch_size: the number of samples to precess at each loop
        :param device: the device type on which to run the Inception model (defaults to 'cpu')
        """
        super(IS, self).__init__(chkpts_root=chkpts_root, crop_fc=False, device=device, n_samples=n_samples,
                                 batch_size=batch_size)
        self.inception_sm = nn.Sequential(
            self.inception,
            nn.Softmax(dim=1)
        )

    # noinspection DuplicatedCode
    def forward(self, dataset: Dataset, gen: nn.Module, gen_transforms: transforms.Compose,
                target_index: Optional[int] = None, condition_indices: Optional[Union[int, tuple]] = None,
                z_dim: Optional[int] = None) -> Tensor:
        """
        Compute the Inception Score of the images generated by the given generator network.
        :param dataset: a torch.utils.data.Dataset object to access real images. Attention: no transforms should be
                        applied when __getitem__ is called since the transforms are different on Inception v3
        :param gen: the Generator network
        :param gen_transforms: the torchvision transforms on which the generator was trained
        :param target_index: NOT used in IS
        :param condition_indices: indices of images that will be passed to the Generator in order to generate fake
                                  images (for image-to-image translation tasks). If set to None, the generator is fed
                                  with random noise.
        :param z_dim: if $condition_indices$ is None, then this is necessary to produce random noise to feed into the
                      DCGAN-like generator
        :return: a scalar torch.Tensor containing the computed IS value
        """
        dataloader = DataLoader(dataset=dataset, batch_size=self.batch_size, shuffle=True)
        gen_transforms_inv = invert_transforms(gen_transforms)

        if self.device == 'cuda' and torch.cuda.is_available():
            torch.cuda.empty_cache()

        cur_samples = 0
        fake_predictions_list = []
        for real_samples in self.tqdm(dataloader, total=self.n_samples // self.batch_size):
            if cur_samples >= self.n_samples:
                break

            cur_batch_size = len(real_samples if condition_indices is None else real_samples[0])

            # Compute predictions on fake
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
            fake_predictions = self.inception_sm(FID.InceptionV3Transforms(fake_output))
            fake_predictions_list.append(fake_predictions.detach().cpu())

            cur_samples += cur_batch_size

        fake_predictions = torch.cat(fake_predictions_list, dim=0)

        # Compute IS
        # Compute marginal distribution, P(y) = 1/|x|*Σ{P(y|x)}, where x ~ P_g
        p_y = torch.mean(fake_predictions, dim=0)
        # Compute D_kl[p(y|xi)||p(y)] for every generated sample xi
        # (credits to hantian_pang, see https://stackoverflow.com/a/54977657/13634700)
        kls = [(p_y_given_xi * (p_y_given_xi / p_y).log()).sum() for p_y_given_xi in fake_predictions]
        return torch.exp(torch.mean(torch.stack(kls), dim=0))


if __name__ == '__main__':
    is_calculator = IS(n_samples=2, batch_size=1)
    _dataset = ICRBCrossPoseDataset(image_transforms=None, pose=True)
    _gen = PGPGGenerator(c_in=6, c_out=3, w_in=128, h_in=128)
    _gen_transforms = ICRBDataset.get_image_transforms(target_shape=128, target_channels=3)
    is_ = is_calculator(dataset=_dataset, gen=_gen, gen_transforms=_gen_transforms, condition_indices=(0, 2))
    print(is_)