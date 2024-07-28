import torch
import itertools
from .base_model import BaseModel
from CellEnMon.util.image_pool import SignalPool
from .networks import define_G, define_D, GANLoss
import numpy as np


class CycleGANModel(BaseModel):
    """
    This class implements the CycleGAN model, for learning CML-to-Gauge translation without paired data.
    """

    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        """Add new dataset-specific options, and rewrite default values for existing options.

        Parameters:
            parser          -- original option parser
            is_train (bool) -- whether training phase or test phase. You can use this flag to add training-specific or test-specific options.

        Returns:
            the modified parser.

        For CycleGAN, in addition to GAN losses, we introduce lambda_A, lambda_B, and lambda_identity for the following losses.
        A (source domain), B (target domain).
        Generators: G_A: A -> B; G_B: B -> A.
        Discriminators: D_A: G_A(A) vs. B; D_B: G_B(B) vs. A.
        Forward cycle loss:  lambda_A * ||G_B(G_A(A)) - A||
        Backward cycle loss: lambda_B * ||G_A(G_B(B)) - B||
        Dropout is not used in the original CycleGAN paper.
        """
        parser.set_defaults(no_dropout=True)  # default CycleGAN did not use dropout
        if is_train:
            parser.add_argument('--lambda_A', type=float, default=10, help='weight for cycle loss (A -> B -> A)')
            parser.add_argument('--lambda_B', type=float, default=10, help='weight for cycle loss (B -> A -> B)')
            parser.add_argument('--lambda_identity', type=float, default=0.0,
                                help='use identity mapping. Setting lambda_identity other than 0 has an effect of scaling the weight of the identity mapping loss. For example, if the weight of the identity loss should be 10 times smaller than the weight of the reconstruction loss, please set lambda_identity = 0.1')

        return parser

    def __init__(self, opt):
        """Initialize the CycleGAN class.

        Parameters:
            opt (Option class)  -- stores all the experiment flags; needs to be a subclass of BaseOptions
        """
        BaseModel.__init__(self, opt)
        # specify the training losses you want to print out. The training/test scripts will call <BaseModel.get_current_losses>
        self.loss_names = ['D_A', 'G_A', 'cycle_A', 'idt_A', 'D_B', 'G_B', 'cycle_B', 'idt_B', 'mse_A', 'mse_B']
        # specify the images you want to save/display. The training/test scripts will call <BaseModel.get_current_visuals>
        visual_names_A = ['real_A', 'fake_B', 'rec_A']
        visual_names_B = ['real_B', 'fake_A', 'rec_B']
        if self.isTrain and self.opt.lambda_identity > 0.0:  # if identity loss is used, we also visualize idt_B=G_A(B) ad idt_A=G_A(B)
            visual_names_A.append('idt_B')
            visual_names_B.append('idt_A')

        self.visual_names = visual_names_A + visual_names_B  # combine visualizations for A and B
        # specify the models you want to save to the disk. The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>.
        if self.isTrain:
            self.model_names = ['G_A', 'G_B', 'D_A', 'D_B']
        else:  # during test time, only load Gs
            self.model_names = ['G_A', 'G_B']

        # define networks (both Generators and discriminators)
        # The naming is different from those used in the paper.
        # Code (vs. paper): G_A (G), G_B (F), D_A (D_Y), D_B (D_X)
        self.netG_A = define_G(opt.input_nc_A, opt.output_nc_A, opt.ngf, opt.netG, opt.norm,
                               not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids,direction="AtoB")
        self.netG_B = define_G(opt.input_nc_B, opt.output_nc_B, opt.ngf, opt.netG, opt.norm,
                               not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids, direction="BtoA")

        if self.isTrain:  # define discriminators
            self.netD_A = define_D(opt.input_nc_B, opt.ndf, opt.netD,
                                   opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)
            self.netD_B = define_D(opt.input_nc_A, opt.ndf, opt.netD,
                                   opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)

        if self.isTrain:
            self.fake_A_pool = SignalPool(opt.pool_size)  # create signal buffer to store previously generated signals
            self.fake_B_pool = SignalPool(opt.pool_size)  # create signal buffer to store previously generated signals
            # define loss functions
            self.criterionGAN = GANLoss(opt.gan_mode).to(self.device)  # define GAN loss.
            self.criterionCycle = torch.nn.L1Loss()
            self.criterionIdt = torch.nn.L1Loss()
            self.mse = torch.nn.MSELoss()
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G = torch.optim.Adam(itertools.chain(self.netG_A.parameters(), self.netG_B.parameters()),
                                                lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D = torch.optim.Adam(itertools.chain(self.netD_A.parameters(), self.netD_B.parameters()),
                                                lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)

    def set_input(self, input, isTrain=True):
        """Unpack input data from the dataloader and perform necessary pre-processing steps.

        Parameters:
            input (dict): include the data itself and its metadata information.

        The option 'direction' can be used to swap domain A and domain B.
        """
        AtoB = self.opt.direction == 'AtoB'
        self.real_A = input['A' if AtoB else 'B'].to(self.device) if isTrain else input["attenuation_sample" if AtoB else 'rain_rate_sample'].to(self.device)
        self.real_B = input['B' if AtoB else 'A'].to(self.device) if isTrain else input['rain_rate_sample' if AtoB else 'attenuation_sample'].to(self.device)
        self.gague = input['gague']
        self.link = input['link']
        self.t = input['Time']
        
        if isTrain:
            self.alpha=0.2
            self.metadata_A = input['metadata_A' if AtoB else 'metadata_B'].to(self.device)
            self.metadata_B = input['metadata_B' if AtoB else 'metadata_A'].to(self.device)
            self.rain_rate_prob = 1 - input['rain_rate'].to(self.device)
            self.attenuation_prob = 1 - input['attenuation'].to(self.device)

            
            self.link_norm_metadata=input['link_norm_metadata']
            self.link_metadata=input['link_metadata']
            self.link_full_name=input['link_full_name'][0]
            self.link_center_metadata=input['link_center_metadata']


            self.gague_norm_metadata=input['gague_norm_metadata']
            self.gague_metadata=input['gague_metadata']
            self.gague_full_name=input['gague_full_name'][0]

            self.data_transformation = input['data_transformation']
            self.metadata_transformation = input['metadata_transformation']
            

    def forward(self):
        """Run forward pass; called by both functions <optimize_parameters> and <test>."""
        
        
#         output1, output2 = output[:, 0:1, :], output[:, 0:1, :]
#         output2 = torch.sigmoid(output2)
#         output2 = (output2 > 0.1).float() * output2
#         return torch.cat([output1, output2], dim=1)

        
        
#         self.fake_B = self.netG_A(self.real_A)[:, :1, :]  # G_A(A)
#         self.fake_B_classification_vector = self.netG_A(self.real_A)[:, 1:2, :]
#         self.fake_A = self.netG_B(self.real_B)[:, :1, :]  # G_B(B)            
#         self.fake_A_classification_vector = self.netG_B(self.real_B)[:, 1:2, :]

            
            
        self.fake_B = self.netG_A(self.real_A)  # G_A(A)
        self.fake_B_classification_vector = torch.sigmoid(self.fake_B).float()
        self.rec_A = self.netG_B(self.fake_B)  # G_B(G_A(A))
        
        self.fake_A = self.netG_B(self.real_B)  # G_B(B)
        self.fake_A_classification_vector = torch.sigmoid(self.fake_A).float()
        self.rec_B = self.netG_A(self.fake_A)  # G_A(G_B(B))
        
        
        

    def backward_D_basic(self, netD, real, fake):
        """Calculate GAN loss for the discriminator

        Parameters:
            netD (network)      -- the discriminator D
            real (tensor array) -- real images
            fake (tensor array) -- images generated by a generator

        Return the discriminator loss.
        We also call loss_D.backward() to calculate the gradients.
        """
        # Real
        pred_real = netD(real)
        loss_D_real = self.criterionGAN(pred_real, True)
        # Fake
        pred_fake = netD(fake.detach())
        loss_D_fake = self.criterionGAN(pred_fake, False)
        # Combined loss and calculate gradients
        loss_D = loss_D_real + loss_D_fake
        loss_D.backward()
        return loss_D

    def backward_D_A(self):
        """Calculate GAN loss for discriminator D_A"""
        fake_B = self.fake_B_pool.query(self.fake_B)
        self.loss_D_A = self.backward_D_basic(self.netD_A, self.real_B, fake_B)

    def backward_D_B(self):
        """Calculate GAN loss for discriminator D_B"""
        fake_A = self.fake_A_pool.query(self.fake_A)
        self.loss_D_B = self.backward_D_basic(self.netD_B, self.real_A, fake_A)

    def backward_G(self):
        """Calculate the loss for generators G_A and G_B"""
        lambda_idt = self.opt.lambda_identity
        lambda_A = 10
        lambda_B = 10
        # Identity loss
        if lambda_idt > 0:
            # G_A should be identity if real_B is fed: ||G_A(B) - B||
            self.idt_A = self.netG_A(self.real_B)
            self.loss_idt_A = self.criterionIdt(self.idt_A, self.real_B) * lambda_B * lambda_idt
            # G_B should be identity if real_A is fed: ||G_B(A) - A||
            self.idt_B = self.netG_B(self.real_A)
            self.loss_idt_B = self.criterionIdt(self.idt_B, self.real_A) * lambda_A * lambda_idt
        else:
            self.loss_idt_A = 0
            self.loss_idt_B = 0
        
        
        th=0.25
        
        # GAN loss D_A(G_A(A))
#         self.loss_G_A = self.criterionGAN(self.netD_A(torch.where(self.real_B > th, self.fake_B, torch.zeros_like(self.fake_B))), True) 
        self.loss_G_A = self.criterionGAN(self.netD_A(self.fake_B), True) 
        # GAN loss D_B(G_B(B))
        assert self.fake_B_classification_vector.shape == self.real_B.shape, "Shape mismatch between fake_B and real_B"
        
        
        self.bce_criterion = torch.nn.BCELoss()
        classification_vector=torch.where(self.fake_B_classification_vector > 0.1, self.fake_B_classification_vector, torch.zeros_like(self.fake_B_classification_vector))
        
        self.loss_G_B = self.criterionGAN(self.netD_B(self.fake_A), True)  + self. bce_criterion(classification_vector, self.real_B)
        
        
        # 
        # Forward cycle loss || G_B(G_A(A)) - A||
        

        mmin=torch.Tensor(self.data_transformation['link']['min']).cuda()
        mmax=torch.Tensor(self.data_transformation['link']['max']).cuda()
        fake_B_max=torch.max(self.fake_B)
        fake_B_unnormalized=self.min_max_inv_transform(x=fake_B_max,mmin=mmin,mmax=mmax)

        
        self.loss_cycle_A = self.criterionCycle(self.real_A, self.rec_A ) * lambda_A #* self.attenuation_prob
        #(self.alpha + 1 -self.func_fit(x=fake_B_unnormalized,a=self.a_rain, b=self.b_rain,c=self.c_rain))
        # Backward cycle loss || G_A(G_B(B)) - B||
        
        

        #torch.where(self.real_B > th, tensor, torch.zeros_like(tensor))
        self.loss_cycle_B = self.criterionCycle(self.real_B,torch.where(self.real_B > th, self.rec_B, torch.zeros_like(self.rec_B))) * lambda_B  #* self.rain_rate_prob
        # combined loss and calculate gradients
        self.loss_G = self.loss_G_A + self.loss_G_B + self.loss_cycle_A + self.loss_cycle_B + self.loss_idt_A + self.loss_idt_B
        self.loss_G.backward()
        self.loss_mse_A = self.mse(self.fake_A, self.real_A)
        
        
        tensor=self.fake_B
        self.loss_mse_B = self.mse(self.real_B, torch.where(tensor > th, tensor, torch.zeros_like(tensor)))
   
    
    def min_max_inv_transform(self,x, mmin, mmax):
        return (x+1) * (mmax - mmin) * 0.5 + mmin
    
    def func_fit(self, x, a, b, c):
        return a * torch.exp(-b * x) + c
    
    def optimize_parameters(self, is_train=True):
        """Calculate losses, gradients, and update network weights; called in every training iteration"""
        # forward
        if is_train:
            self.forward()  # compute fake images and reconstruction images.
            # G_A and G_B
            self.set_requires_grad([self.netD_A, self.netD_B], False)  # Ds require no gradients when optimizing Gs
            self.optimizer_G.zero_grad()  # set G_A and G_B's gradients to zero
            self.backward_G()  # calculate gradients for G_A and G_B
            self.optimizer_G.step()  # update G_A and G_B's weights
            # D_A and D_B
            self.set_requires_grad([self.netD_A, self.netD_B], True)
            self.optimizer_D.zero_grad()  # set D_A and D_B's gradients to zero
            self.backward_D_A()  # calculate gradients for D_A
            self.backward_D_B()  # calculate graidents for D_B
            self.optimizer_D.step()  # update D_A and D_B's weights
        else:
            with torch.no_grad():
                self.forward()