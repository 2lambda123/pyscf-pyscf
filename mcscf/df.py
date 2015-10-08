#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

import time
import ctypes
import numpy
import pyscf.lib
from pyscf.ao2mo import _ao2mo
from pyscf.mcscf import mc1step
from pyscf.mcscf import mc_ao2mo
from pyscf.scf import dfhf
from pyscf import df


def density_fit(casscf, auxbasis='weigend'):
    '''For the given CASSCF object, update the J, K matrix constructor with
    corresponding density fitting integrals.

    Args:
        casscf : an CASSCF object

    Kwargs:
        auxbasis : str

    Returns:
        An CASSCF object with a modified J, K matrix constructor which uses density
        fitting integrals to compute J and K

    Examples:

    >>> mol = gto.M(atom='H 0 0 0; F 0 0 1', basis='ccpvdz', verbose=0)
    >>> mf = scf.RHF(mol)
    >>> mf.scf()
    >>> mc = mcscf.density_fit(mcscf.CASSCF(mf, 4, 4))
    -100.005306000435510
    '''

    class CASSCF(casscf.__class__):
        def __init__(self):
            self.__dict__.update(casscf.__dict__)
            self.auxbasis = auxbasis
            #self.grad_update_dep = 0
            self._cderi = None
            self._keys = self._keys.union(['auxbasis'])

        def ao2mo(self, mo):
            t0 = (time.clock(), time.time())
            ncore = self.ncore
            log = pyscf.lib.logger.Logger(self.stdout, self.verbose)
            # using dm=[], a hacky call to dfhf.get_jk, to generate self._cderi
            self.get_jk(self.mol, [])
            if log.verbose >= pyscf.lib.logger.DEBUG1:
                t1 = log.timer('Generate density fitting integrals', *t0)

            eris = mc_ao2mo._ERIS(self, mo, 'incore', level=2)

            t0 = (time.clock(), time.time())
            mo = numpy.asarray(mo, order='F')
            nao, nmo = mo.shape
            eris.j_pc = numpy.zeros((nmo,ncore))
            k_cp = numpy.zeros((ncore,nmo))
            fmmm = _ao2mo._fpointer('AO2MOmmm_nr_s2_iltj')
            fdrv = _ao2mo.libao2mo.AO2MOnr_e2_drv
            ftrans = _ao2mo._fpointer('AO2MOtranse2_nr_s2kl')
            bufs1 = numpy.empty((dfhf.BLOCKDIM,nmo,nmo))
            with df.load(self._cderi) as feri:
                for b0, b1 in dfhf.prange(0, self._naoaux, dfhf.BLOCKDIM):
                    eri1 = numpy.array(feri[b0:b1], copy=False)
                    buf = bufs1[:b1-b0]
                    if log.verbose >= pyscf.lib.logger.DEBUG1:
                        t1 = log.timer('load buf %d:%d'%(b0,b1), *t1)
                    fdrv(ftrans, fmmm,
                         buf.ctypes.data_as(ctypes.c_void_p),
                         eri1.ctypes.data_as(ctypes.c_void_p),
                         mo.ctypes.data_as(ctypes.c_void_p),
                         ctypes.c_int(b1-b0), ctypes.c_int(nao),
                         ctypes.c_int(0), ctypes.c_int(nmo),
                         ctypes.c_int(0), ctypes.c_int(nmo),
                         ctypes.c_void_p(0), ctypes.c_int(0))
                    if log.verbose >= pyscf.lib.logger.DEBUG1:
                        t1 = log.timer('transform [%d:%d]'%(b0,b1), *t1)
                    bufd = numpy.einsum('kii->ki', buf).copy()
                    #:eris.j_pc += numpy.einsum('ki,kj->ij', bufd, bufd[:,:ncore])
                    pyscf.lib.dot(bufd.T, numpy.asarray(bufd[:,:ncore],order='C'),
                                  1, eris.j_pc, 1)
                    k_cp += numpy.einsum('kij,kij->ij', buf[:,:ncore], buf[:,:ncore])
                    if log.verbose >= pyscf.lib.logger.DEBUG1:
                        t1 = log.timer('j_pc and k_pc', *t1)
                    eri1 = None
            eris.k_pc = k_cp.T.copy()
            log.timer('ao2mo density fit part', *t0)
            return eris

# We don't modify self._scf because it changes self.h1eff function.
# We only need approximate jk for self.update_jk_in_ah
        def get_jk(self, mol, dm, hermi=1):
            return dfhf.get_jk_(self, mol, dm, hermi=hermi)

    return CASSCF()



if __name__ == '__main__':
    from pyscf import gto
    from pyscf import scf
    from pyscf.mcscf import addons

    mol = gto.Mole()
    mol.atom = [
        ['O', ( 0., 0.    , 0.   )],
        ['H', ( 0., -0.757, 0.587)],
        ['H', ( 0., 0.757 , 0.587)],]
    mol.basis = {'H': 'cc-pvdz',
                 'O': 'cc-pvdz',}
    mol.build()

    m = scf.RHF(mol)
    ehf = m.scf()
    mc = density_fit(mc1step.CASSCF(m, 6, 4))
    mc.verbose = 4
    mo = addons.sort_mo(mc, m.mo_coeff, (3,4,6,7,8,9), 1)
    emc = mc.mc1step(mo)[0]
    print(ehf, emc, emc-ehf)
    #-76.0267656731 -76.0873922924 -0.0606266193028
    print(emc - -76.0873923174, emc - -76.0926176464)

    mc = density_fit(mc1step.CASSCF(m, 6, (3,1)))
    mc.verbose = 4
    emc = mc.mc2step(mo)[0]
    print(emc - -75.7155632535814)
