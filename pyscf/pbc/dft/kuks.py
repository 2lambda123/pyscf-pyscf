#!/usr/bin/env python
# Copyright 2014-2019 The PySCF Developers. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
Non-relativistic Restricted Kohn-Sham for periodic systems with k-point sampling

See Also:
    pyscf.pbc.dft.rks.py : Non-relativistic Restricted Kohn-Sham for periodic
                           systems at a single k-point
'''


import numpy as np
from pyscf import lib
from pyscf.lib import logger
from pyscf.pbc.scf import khf, kuhf
from pyscf.pbc.dft import gen_grid
from pyscf.pbc.dft import rks
from pyscf.pbc.dft import multigrid
from pyscf import __config__


def get_veff(ks, cell=None, dm=None, dm_last=0, vhf_last=0, hermi=1,
             kpts=None, kpts_band=None):
    '''Coulomb + XC functional for UKS.  See pyscf/pbc/dft/uks.py
    :func:`get_veff` fore more details.
    '''
    if cell is None: cell = ks.cell
    if dm is None: dm = ks.make_rdm1()
    if kpts is None: kpts = ks.kpts
    t0 = (logger.process_clock(), logger.perf_counter())

    ni = ks._numint
    if ks.nlc or ni.libxc.is_nlc(ks.xc):
        raise NotImplementedError(f'NLC functional {ks.xc} + {ks.nlc}')

    hybrid = ni.libxc.is_hybrid_xc(ks.xc)

    if not hybrid and isinstance(ks.with_df, multigrid.MultiGridFFTDF):
        n, exc, vxc = multigrid.nr_uks(ks.with_df, ks.xc, dm, hermi,
                                       kpts, kpts_band,
                                       with_j=True, return_j=False)
        logger.debug(ks, 'nelec by numeric integration = %s', n)
        t0 = logger.timer(ks, 'vxc', *t0)
        return vxc

    # ndim = 4 : dm.shape = ([alpha,beta], nkpts, nao, nao)
    ground_state = (dm.ndim == 4 and dm.shape[0] == 2 and kpts_band is None)

    if ks.grids.non0tab is None:
        ks.grids.build(with_non0tab=True)
        if (isinstance(ks.grids, gen_grid.BeckeGrids) and
            ks.small_rho_cutoff > 1e-20 and ground_state):
            ks.grids = rks.prune_small_rho_grids_(ks, cell, dm, ks.grids, kpts)
        t0 = logger.timer(ks, 'setting up grids', *t0)

    if hermi == 2:  # because rho = 0
        n, exc, vxc = (0,0), 0, 0
    else:
        max_memory = ks.max_memory - lib.current_memory()[0]
        n, exc, vxc = ks._numint.nr_uks(cell, ks.grids, ks.xc, dm, 0, hermi,
                                        kpts, kpts_band, max_memory=max_memory)
        logger.debug(ks, 'nelec by numeric integration = %s', n)
        t0 = logger.timer(ks, 'vxc', *t0)

    nkpts = len(kpts)
    weight = 1. / nkpts

    if not hybrid:
        vj = ks.get_j(cell, dm[0]+dm[1], hermi, kpts, kpts_band)
        vxc += vj
    else:
        omega, alpha, hyb = ks._numint.rsh_and_hybrid_coeff(ks.xc, spin=cell.spin)
        if getattr(ks.with_df, '_j_only', False) and nkpts > 1: # for GDF and MDF
            ks.with_df._j_only = False
            if ks.with_df._cderi is not None:
                logger.warn(ks, 'df.j_only cannot be used with hybrid '
                            'functional. Rebuild cderi')
                ks.with_df.build()
        vj, vk = ks.get_jk(cell, dm, hermi, kpts, kpts_band)
        vj = vj[0] + vj[1]
        vk *= hyb
        if omega != 0:
            vklr = ks.get_k(cell, dm, hermi, kpts, kpts_band, omega=omega)
            vklr *= (alpha - hyb)
            vk += vklr
        vxc += vj - vk

        if ground_state:
            exc -= (np.einsum('Kij,Kji', dm[0], vk[0]) +
                    np.einsum('Kij,Kji', dm[1], vk[1])).real * .5 * weight

    if ground_state:
        ecoul = np.einsum('Kij,Kji', dm[0]+dm[1], vj).real * .5 * weight
    else:
        ecoul = None

    vxc = lib.tag_array(vxc, ecoul=ecoul, exc=exc, vj=None, vk=None)
    return vxc

def energy_elec(mf, dm_kpts=None, h1e_kpts=None, vhf=None):
    if h1e_kpts is None: h1e_kpts = mf.get_hcore(mf.cell, mf.kpts)
    if dm_kpts is None: dm_kpts = mf.make_rdm1()
    if vhf is None or getattr(vhf, 'ecoul', None) is None:
        vhf = mf.get_veff(mf.cell, dm_kpts)

    weight = 1./len(h1e_kpts)
    e1 = weight *(np.einsum('kij,kji', h1e_kpts, dm_kpts[0]) +
                  np.einsum('kij,kji', h1e_kpts, dm_kpts[1]))
    ecoul = vhf.ecoul
    tot_e = e1 + ecoul + vhf.exc
    mf.scf_summary['e1'] = e1.real
    mf.scf_summary['coul'] = ecoul.real
    mf.scf_summary['exc'] = vhf.exc.real
    logger.debug(mf, 'E1 = %s  Ecoul = %s  Exc = %s', e1, ecoul, vhf.exc)
    if khf.CHECK_COULOMB_IMAG and abs(ecoul.imag > mf.cell.precision*10):
        logger.warn(mf, "Coulomb energy has imaginary part %s. "
                    "Coulomb integrals (e-e, e-N) may not converge !",
                    ecoul.imag)
    return tot_e.real, vhf.ecoul + vhf.exc

@lib.with_doc(kuhf.get_rho.__doc__)
def get_rho(mf, dm=None, grids=None, kpts=None):
    from pyscf.pbc.dft import krks
    if dm is None:
        dm = mf.make_rdm1()
    return krks.get_rho(mf, dm[0]+dm[1], grids, kpts)


class KUKS(kuhf.KUHF, rks.KohnShamDFT):
    '''RKS class adapted for PBCs with k-point sampling.
    '''

    get_veff = get_veff
    energy_elec = energy_elec
    get_rho = get_rho

    def __init__(self, cell, kpts=np.zeros((1,3)), xc='LDA,VWN',
                 exxdiv=getattr(__config__, 'pbc_scf_SCF_exxdiv', 'ewald')):
        kuhf.KUHF.__init__(self, cell, kpts, exxdiv=exxdiv)
        rks.KohnShamDFT.__init__(self, xc)

    def dump_flags(self, verbose=None):
        kuhf.KUHF.dump_flags(self, verbose)
        rks.KohnShamDFT.dump_flags(self, verbose)
        return self

    def nuc_grad_method(self):
        from pyscf.pbc.grad import kuks
        return kuks.Gradients(self)

    def to_hf(self):
        '''Convert to KUHF object.'''
        from pyscf.pbc import scf
        return self._transfer_attrs_(scf.KUHF(self.cell, self.kpts))

    to_khf = to_hf


if __name__ == '__main__':
    from pyscf.pbc import gto
    cell = gto.Cell()
    cell.unit = 'A'
    cell.atom = 'C 0.,  0.,  0.; C 0.8917,  0.8917,  0.8917'
    cell.a = '''0.      1.7834  1.7834
                1.7834  0.      1.7834
                1.7834  1.7834  0.    '''

    cell.basis = 'gth-szv'
    cell.pseudo = 'gth-pade'
    cell.verbose = 7
    cell.output = '/dev/null'
    cell.build()
    mf = KUKS(cell, cell.make_kpts([2,1,1]))
    print(mf.kernel())
