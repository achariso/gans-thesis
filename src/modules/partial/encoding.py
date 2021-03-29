import torch.nn as nn
from torch import Tensor


class ContractingBlock(nn.Module):
    """
    ContractingBlock Class
    Performs a convolution followed by a max pool operation and an optional instance norm.
    """

    def __init__(self, c_in: int, use_norm: bool = True, kernel_size: int = 3, activation: str = 'relu',
                 c_out: int = None, stride: int = 2, padding: int = 1, padding_mode: str = 'reflect',
                 norm_type: str = 'instance'):
        """
        ContractingBlock class constructor.
        :param (int) c_in: the number of channels to expect from a given input
        :param (bool) use_norm: indicates if InstanceNormalization2d is applied or not after Conv2d layer
        :param (int) kernel_size: filter (kernel) size
        :param (str) activation: type of activation function used (supported: 'relu', 'lrelu')
        :param (optional) c_out: set to None to output 2*c_in channels
        :param (int) stride: stride as integer (same for W and H)
        :param (int) padding: padding as integer (same for W and H)
        :param (int) padding_mode: see torch.nn.Conv2d of more info on this argument
        :param (str) norm_type: available types are 'batch', 'instance', 'pixel', 'layer'
        """
        super(ContractingBlock, self).__init__()
        c_out = c_in * 2 if not c_out else c_out
        _layers = [nn.Conv2d(c_in, c_out, kernel_size=kernel_size, padding=padding, stride=stride,
                             padding_mode=padding_mode), ]
        if use_norm:
            from modules.partial.normalization import PixelNorm2d, LayerNorm2d
            switcher = {
                'batch': nn.BatchNorm2d(c_out),
                'instance': nn.InstanceNorm2d(c_out),
                'pixel': PixelNorm2d(),
                'layer': LayerNorm2d(c_out),
            }
            _layers.append(switcher[norm_type])
        _layers.append(nn.ReLU() if activation == 'relu' else nn.LeakyReLU(0.2))
        self.contracting_block = nn.Sequential(*_layers)

    def forward(self, x: Tensor) -> Tensor:
        """
        Function for completing a forward pass of ContractingBlock:
        Given an image tensor, completes a contracting block and returns the transformed tensor.
        :param x: image tensor of shape (N, C_in, H, W)
        :return: transformed image tensor of shape (N, C_in*2, H/2, W/2)
        """
        return self.contracting_block(x)


class UNETContractingBlock(nn.Module):
    """
    UNETContractingBlock Class:
    Performs two convolutions followed by a max pool operation.
    Attention: Unlike UNET paper, we add padding=1 to Conv2d layers to make a "symmetric" version of UNET.
    """

    def __init__(self, c_in: int, use_bn: bool = True, use_dropout: bool = False, kernel_size: int = 3,
                 activation: str = 'lrelu'):
        """
        UNETContractingBlock class constructor.
        :param c_in: number of input channels
        :param use_bn: indicates if Batch Normalization is applied or not after Conv2d layer
        :param use_dropout: indicates if Dropout is applied or not after Conv2d layer
        :param kernel_size: filter (kernel) size
        :param activation: type of activation function used (supported: 'relu', 'lrelu')
        """
        super(UNETContractingBlock, self).__init__()
        self.unet_contracting_block = nn.Sequential(
            # 1st convolution layer
            nn.Conv2d(c_in, c_in * 2, kernel_size=kernel_size, stride=1, padding=1),
            nn.BatchNorm2d(c_in * 2) if use_bn else nn.Identity(),
            nn.Dropout() if use_dropout else nn.Identity(),
            nn.ReLU() if activation == 'relu' else nn.LeakyReLU(0.2),
            # 2nd convolution layer
            nn.Conv2d(c_in * 2, c_in * 2, kernel_size=kernel_size, stride=1, padding=1),
            nn.BatchNorm2d(c_in * 2) if use_bn else nn.Identity(),
            nn.Dropout() if use_dropout else nn.Identity(),
            nn.ReLU() if activation == 'relu' else nn.LeakyReLU(0.2),
            # Downsampling (using MaxPool) layer (preparing for next block)
            nn.MaxPool2d(kernel_size=2, stride=2)
        )

    def forward(self, x: Tensor) -> Tensor:
        """
        Function for completing a forward pass of UNETContractingBlock:
        Given an image tensor, completes a contracting block and returns the transformed tensor.
        :param x: image tensor of shape (N, C_in, H, W)
        :return: transformed image tensor of shape (N, C_in*2, H/2, W/2)
        """
        return self.unet_contracting_block(x)


class MLPBlock(nn.Module):
    """
    MLPBlock Class
    This is a Multi-Layer Perceptron block composed of (Linear-Relu)*2+Linear.
    """

    def __init__(self, in_dim: int, hidden_dim: int, out_dim: int, activation: str = 'relu'):
        """
        MLPBlock class constructor.
        :param in_dim: number of input neurons
        :param hidden_dim: number of neurons in hidden layers
        :param out_dim: number of neurons in output layer
        :param activation: type of activation function used (supported: 'relu', 'lrelu')
        """
        super(MLPBlock, self).__init__()
        self.mlp_block = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.ReLU() if activation == 'relu' else nn.LeakyReLU(0.2, inplace=True),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU() if activation == 'relu' else nn.LeakyReLU(0.2, inplace=True),
            nn.Linear(hidden_dim, out_dim),
        )

    def forward(self, x: Tensor) -> Tensor:
        """
        Function for completing a forward pass of MLPBlock:
        Given a tensor, completes a MLP block and returns the transformed tensor.
        :param x: tensor of shape (N, in_dim)
        :return: transformed tensor of shape (N, out_dim)
        """
        return self.mlp_block(x)
