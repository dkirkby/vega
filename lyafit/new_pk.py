import numpy as np
import scipy as sp
from pkg_resources import resource_filename
from . import utils

# ! This should be moved here and propagated to FHT
muk = utils.muk

class PowerSpectrum:
    """Power Spectrum computation and handling
    """
    # ! Slow operations should be kept in init as that is only called once
    # ! Compute is called many times and should be fast
    # * Extensions should be have their separate method which is
    # * called from compute

    def __init__(self, config, tracer1, tracer2, dataset_name=None, pk_fid=None):
        """[summary]

        Parameters
        ----------
        config : dict
            pk config object
        tracer1 : string
            Name of tracer 1
        tracer2 : string
            Name of tracer 1
        """
        self._config = config
        self._tracer1 = tracer1
        self._tracer2 = tracer2

        pk_model = None
        if 'model-pk' in self._config.keys():
            pk_model = self._config['model-pk']

        self._hcd_model = None
        self._add_uv = False
        if pk_model is None:
            if 'hcd_model' in self._config['keys']:
                self._hcd_model = self._config['model-hcd']
            self._add_uv = self._config.get('add uv', False)
        else:
            if 'hcd' in pk_model:
                self._hcd_model = pk_model
            if 'uv' in pk_model:
                self._add_uv = True

        self._Fvoigt_data = None
        if 'fvoigt_model' in self._config:
            fvoigt_model = self._config['fvoight_model']
            path = '{}/models/fvoigt_models/Fvoigt_{}.txt'.format(
                            resource_filename('lyafit', 'lyafit'),
                            fvoigt_model)

            self._Fvoigt_data = sp.loadtxt(path)

        self._dataset_name = dataset_name
        self._pk_fid = pk_fid
        self._pk_Gk = None
        pass

    def compute(self, k, pk_lin, params):
        """Handles computation of the Power Spectrum for the input tracers

        Parameters
        ----------
        k : 1D array
            k bins for the pk
        pk_lin : 1D array
            Linear Power Spectrum
        params : dict
            Computation parameters

        Returns
        -------
        1D Array
            pk
        """
        self._k = k
        self._params = params

        bias_beta = utils.bias_beta(params, self._tracer1, self._tracer2)
        bias1, beta1, bias2, beta2 = bias_beta

        # Add UV model
        if self._add_uv:
            if self._tracer1['name'] == 'LYA':
                bias1, beta1 = self.bias_beta_uv(bias1, beta1)
            if self._tracer2['name'] == 'LYA':
                bias2, beta2 = self.bias_beta_uv(bias2, beta2)

        # Add HCD model
        if self._hcd_model is not None:
            # extract HCD pars
            bias_hcd, beta_hcd, L0 = utils.get_hcd_pars(self._params)

            # Check which model we need
            if 'Rogers' in self._hcd_model:
                F_hcd = self.hcd_Rogers2018(L0)
            elif 'mask' in self._hcd_model:
                assert self._Fvoigt_data is not None
                F_hcd = self.hcd_no_mask(L0)
            else:
                F_hcd = self.hcd_sinc(L0)

            # Update bias/beta
            if self._tracer1['name'] == 'LYA':
                bias1, beta1 = self.bias_beta_hcd(bias1, beta1,
                                                  bias_hcd, beta_hcd, F_hcd)
            if self._tracer2['name'] == 'LYA':
                bias2, beta2 = self.bias_beta_hcd(bias2, beta2,
                                                  bias_hcd, beta_hcd, F_hcd)

        # Compute kaiser model
        pk = pk_lin * self.pk_kaiser(bias1, beta1, bias2, beta2)
        print('kaiser new:', np.sum(pk))
        # TODO gauss smoothing
        # TODO vel dispersion

        # add non linear small scales
        if 'small scale nl' in self._config.keys():
            if 'arinyo' in self._config['small scale nl']:
                pk *= self.dnl_arinyo()
            elif 'mcdonald' in self._config['small scale nl']:
                pk *= self.dnl_mcdonald()
            else:
                print('small scale nl: must be either mcdonald or arinyo')
                raise ValueError('Incorrect \'small scale nl\' specified')

        # model the effect of binning
        if self._pk_Gk is None:
            self._pk_Gk = self.Gk()
        print('Gk new:', np.sum(self._pk_Gk))
        pk *= self._pk_Gk

        # add non linear large scales
        if self._params['peak']:
            print('NL peak new:', np.sum(self.pk_NL()))
            pk *= self.pk_NL()

        return pk

    @staticmethod
    def pk_kaiser(bias1, beta1, bias2, beta2):
        """Compute Kaiser model

        Returns
        -------
        1D Array
            pk kaiser
        """
        pk = bias1 * bias2
        pk = pk * (1 + beta1 * muk**2)
        pk = pk * (1 + beta2 * muk**2)
        return pk

    @staticmethod
    def bias_beta_hcd(bias, beta, bias_hcd, beta_hcd, F_hcd):
        """ Compute effective biases that include HCD modeling

        Parameters
        ----------
        bias : float
            Bias for tracer
        beta : float
            Beta for tracer
        bias_hcd : float
            Bias for HCDs
        beta_hcd : float
            Beta for HCDs
        F_hcd : 1D array
            HCD model function

        Returns
        -------
        (float, float)
            Effective bias and beta
        """
        bias_eff = bias + bias_hcd * F_hcd
        beta_eff = (bias * beta + bias_hcd * beta_hcd * F_hcd)
        beta_eff /= (bias + bias_hcd * F_hcd)

        return bias_eff, beta_eff

    def bias_beta_uv(self, bias, beta):
        """ Compute effective biases that include HCD modeling

        Parameters
        ----------
        bias : float
            Bias for tracer
        beta : float
            Beta for tracer

        Returns
        -------
        (float, float)
            Effective bias and beta
        """
        bias_gamma = self._params["bias_gamma"]
        bias_prim = self._params["bias_prim"]
        lambda_uv = self._params["lambda_uv"]

        W = np.arctan(self._k * lambda_uv) / (self._k * lambda_uv)
        beta_eff = beta / (1 + bias_gamma / bias * W / (1 + bias_prim * W))
        bias_eff = bias + bias_gamma * W / (1 + bias_prim * W)

        return bias_eff, beta_eff

    def hcd_sinc(self, L0):
        """HCD sinc model

        Parameters
        ----------
        L0 : float
            Characteristic length scale of HCDs

        Returns
        -------
        1D Array
            F_hcd
        """
        kp = self._k * muk
        return utils.sinc(kp * L0)

    def hcd_Rogers2018(self, L0):
        """Model the effect of HCD systems with the Fourier transform
        of a Lorentzian profile. Motivated by Rogers et al. (2018).

        Parameters
        ----------
        L0 : float
            Characteristic length scale of HCDs

        Returns
        -------
        1D Array
            F_hcd
        """
        kp = self._k * muk
        return sp.exp(-L0 * kp)

    def hcd_no_mask(self, L0):
        """Use Fvoigt function to fit the DLA in the autocorrelation Lyman-alpha
        without masking them ! (L0 = 1)
        (If you want to mask them use Fvoigt_exp.txt and L0 = 10 as eBOSS DR14)

        Parameters
        ----------
        L0 : float
            Characteristic length scale of HCDs

        Returns
        -------
        1D Array
            F_hcd
        """
        kp = self._k * muk
        k_data = self._Fvoigt_data[:, 0]
        F_data = self._Fvoigt_data[:, 1]

        F_hcd = np.interp(L0 * kp, k_data, F_data, left=0, right=0)
        return F_hcd

    def pk_NL(self):
        """Compute the NL gaussian factor for the peak component

        Returns
        -------
        1D Array
            pk
        """
        kp = self._k * muk
        kt = self._k * sp.sqrt(1 - muk**2)
        st2 = self._params['sigmaNL_per']**2
        sp2 = self._params['sigmaNL_par']**2
        return np.exp(-(kp**2 * sp2 + kt**2 * st2) / 2)

    def dnl_mcdonald(self):
        """Non linear term from McDonald 2003

        Returns
        -------
        1D Array
            D_NL factor
        """
        assert self._tracer1['name'] == "LYA"
        assert self._tracer2['name'] == "LYA"

        kvel = 1.22 * (1 + self._k / 0.923)**0.451
        dnl = (self._k / 6.4)**0.569 - (self._k / 15.3)**2.01
        dnl = dnl - (self._k * muk / kvel)**1.5
        return np.exp(dnl)

    def dnl_arinyo(self):
        """Non linear term from Arinyo et al 2015

        Returns
        -------
        1D Array
            D_NL factor
        """
        assert self._tracer1['name'] == "LYA"
        assert self._tracer2['name'] == "LYA"
        q1 = self._params["dnl_arinyo_q1"]
        kv = self._params["dnl_arinyo_kv"]
        av = self._params["dnl_arinyo_av"]
        bv = self._params["dnl_arinyo_bv"]
        kp = self._params["dnl_arinyo_kp"]

        growth = q1 * self._k**3 * self._pk_fid / (2 * np.pi**2)
        pecvelocity = (self._k / kv)**av * np.fabs(muk)**bv
        pressure = (self._k / kp) * (self._k / kp)
        dnl = sp.exp(growth * (1 - pecvelocity) - pressure)
        return dnl

    def Gk(self):
        """Model the effect of binning of the cf

        Returns
        -------
        1D Array
            G(k)
        """
        L_par = self._params["par binsize {}".format(self._dataset_name)]
        L_per = self._params["per binsize {}".format(self._dataset_name)]

        kp = self._k * muk
        kt = self._k * sp.sqrt(1 - muk**2)
        return utils.sinc(kp * L_par / 2) * utils.sinc(kt * L_per / 2)