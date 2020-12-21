from typing import Optional, Union

import numpy as np
import torch
import torch.utils.data
from scipy.stats import entropy
from torch import nn
# noinspection PyProtectedMember
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

from dataset.deep_fashion import ICRBCrossPoseDataset
from modules.generators.pgpg import PGPGGenerator
from utils.metrics.fid import FID


class IS(FID):
    """
    IS Class:
    This class is used to compute the Inception Score (IS) between two given image sets.
    """

    def __init__(self, chkpts_root: str = '/home/achariso/PycharmProjects/gans-thesis/.checkpoints',
                 device: str = 'cpu', n_samples: int = 512, batch_size: int = 8):
        """
        IS class constructor.
        :param chkpts_root: absolute path to model checkpoints directory
        :param device: the device type on which to run the Inception model (defaults to 'cpu')
        :param n_samples: the total number of samples used to compute the metric (defaults to 512; the higher this
                          number gets, the more accurate the metric is)
        :param batch_size: the number of samples to precess at each loop
        """
        super(IS, self).__init__(chkpts_root=chkpts_root, crop_fc=False, device=device, n_samples=n_samples,
                                 batch_size=batch_size)
        self.inception_sm = nn.Sequential(
            self.inception,
            nn.Softmax(dim=1)
        )

    def forward(self, dataset: Dataset, gen: nn.Module, target_index: Optional[int] = None,
                condition_indices: Optional[Union[int, tuple]] = None, z_dim: Optional[int] = None) -> float:
        """
        Compute the Inception Score of the images generated by the given generator network.
        :param dataset: a torch.utils.data.Dataset object to access real images as inputs to the Generator
        :param gen: the Generator network
        :param target_index: NOT used in IS
        :param condition_indices: indices of images that will be passed to the Generator in order to generate fake
                                  images (for image-to-image translation tasks). If set to None, the generator is fed
                                  with random noise.
        :param z_dim: if $condition_indices$ is None, then this is necessary to produce random noise to feed into the
                      DCGAN-like generator
        :return: a scalar value with the computed IS
        """
        gen.eval()
        dataloader = DataLoader(dataset=dataset, batch_size=self.batch_size, shuffle=True)

        cur_samples = 0
        fake_predictions_list = []
        with torch.no_grad():
            for real_samples in tqdm(dataloader, total=self.n_samples // self.batch_size):
                if cur_samples >= self.n_samples:
                    break

                cur_batch_size = len(real_samples if condition_indices is None else real_samples[0])
                gen_inputs = [real_samples[_i] for _i in condition_indices] if condition_indices is not None else \
                    torch.randn(cur_batch_size, z_dim)

                # Compute predictions on fake
                gen_inputs = [gen_input.to(self.device) for gen_input in gen_inputs] if condition_indices is not None \
                    else gen_inputs.to(self.device)
                if self.device.startswith('cuda'):
                    with torch.cuda.amp.autocast:
                        fake_output = gen(*gen_inputs)
                        if type(fake_output) == tuple or type(fake_output) == list:
                            fake_output = fake_output[-1]
                        fake_predictions = self.inception_sm(fake_output)
                else:
                    fake_output = gen(*gen_inputs)
                    if type(fake_output) == tuple or type(fake_output) == list:
                        fake_output = fake_output[-1]
                    fake_predictions = self.inception_sm(fake_output)
                fake_predictions_list.append(fake_predictions.detach().cpu())

                cur_samples += cur_batch_size

            fake_predictions = torch.cat(fake_predictions_list, dim=0).numpy()

        # Compute IS
        # Compute marginal distribution, P(y) = 1/|x|*Σ{P(y|x)}, where x ~ P_g
        p_y = np.mean(fake_predictions, axis=0)
        # Compute D_kl[p(y|xi)||p(y)] for every generated sample xi
        kls = [entropy(p_y_given_xi, p_y) for p_y_given_xi in fake_predictions]
        return np.exp(np.mean(kls))


if __name__ == '__main__':
    _is = IS(n_samples=2, batch_size=1)
    _dataset = ICRBCrossPoseDataset(image_transforms=IS.InceptionV3Transforms, pose=True)
    _gen = PGPGGenerator(c_in=6, c_out=3)
    score = _is(_dataset, _gen, condition_indices=(0, 2))
    print(score)
