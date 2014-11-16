#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

import tempfile
import time
import numpy
import h5py
import pyscf.lib
import pyscf.ao2mo
import pyscf.fci.direct_spin0


def extract_orbs(mo_coeff, ncas, nelecas, ncore):
    nocc = ncore + ncas
    mo_core = mo_coeff[:,:ncore]
    mo_cas = mo_coeff[:,ncore:nocc]
    mo_vir = mo_coeff[:,nocc:]
    return mo_core, mo_cas, mo_vir

def kernel(casci, mo_coeff, ci0=None, verbose=None, fciRestart=False):
    if verbose is None:
        verbose = casci.verbose
    log = pyscf.lib.logger.Logger(casci.stdout, verbose)
    t0 = (time.clock(), time.time())
    log.debug('Start CASCI')

    ncas = casci.ncas
    nelecas = casci.nelecas
    ncore = casci.ncore
    mo_core, mo_cas, mo_vir = extract_orbs(mo_coeff, ncas, nelecas, ncore)

    # 1e
    hcore = casci.get_hcore()
    if mo_core.size == 0:
        corevhf = 0
        energy_core = 0
    else:
        core_dm = numpy.dot(mo_core, mo_core.T) * 2
        corevhf = casci.get_veff(core_dm)
        energy_core = pyscf.lib.trace_ab(core_dm, hcore) \
                + pyscf.lib.trace_ab(core_dm, corevhf) * .5
    h1eff = reduce(numpy.dot, (mo_cas.T, hcore+corevhf, mo_cas))
    t1 = log.timer('effective h1e in CAS space', *t0)

    # 2e
    eri_cas = casci.ao2mo(mo_cas)
    t1 = log.timer('integral transformation to CAS space', *t1)

    # FCI
    e_cas, fcivec = casci.fcisolver.kernel(h1eff, eri_cas, ncas, nelecas, ci0=ci0, fciRestart=fciRestart)

    t1 = log.timer('FCI solver', *t1)
    e_tot = e_cas + energy_core
    log.info('CASCI E = %.15g', e_tot)
    log.timer('CASCI', *t0)
    return e_tot, e_cas, fcivec


class CASCI(object):
    def __init__(self, mol, mf, ncas, nelecas, ncore=None):
        self.mol = mol
        self._scf = mf
        self.verbose = mol.verbose
        self.stdout = mol.stdout
        self.max_memory = mf.max_memory
        self.ncas = ncas
        if isinstance(nelecas, int):
            assert(nelecas%2 == 0)
            self.nelecas = (nelecas/2,nelecas/2)
        else:
            self.nelecas = (nelecas[0],nelecas[1])
        if ncore is None:
            ncorelec = mol.nelectron - (self.nelecas[0]+self.nelecas[1])
            assert(ncorelec % 2 == 0)
            self.ncore = ncorelec / 2
        else:
            self.ncore = ncore
        #TODO: for FCI solver
        self.ci_lindep = 1e-14
        self.ci_max_cycle = 30
        self.ci_conv_threshold = 1e-8
        self.fcisolver = pyscf.fci.direct_spin0

        self.mo_coeff = mf.mo_coeff
        self.ci = None
        self.e_tot = 0

        self._keys = set(self.__dict__.keys() + ['_keys'])

    def dump_flags(self):
        log = pyscf.lib.logger.Logger(self.stdout, self.verbose)
        log.info('')
        log.info('******** CASSCF flags ********')
        nvir = self.mo_coeff.shape[1] - self.ncore - self.ncas
        log.info('CAS (%de+%de, %do), ncore = %d, nvir = %d', \
                 self.nelecas[0], self.nelecas[1], self.ncas, self.ncore, nvir)
        log.info('CI max. cycles = %d', self.ci_max_cycle)
        log.info('CI conv_threshold = %g', self.ci_conv_threshold)
        log.info('CI linear dependence = %g', self.ci_lindep)
        #log.info('CI level shift = %d', self.ci_level_shift)
        log.info('max_memory %d (MB)', self.max_memory)

    def get_hcore(self, mol=None):
        return self._scf.get_hcore(mol)

    def get_veff(self, dm):
        return self._scf.get_veff(self.mol, dm)

    def ao2mo(self, mo):
        nao, nmo = mo.shape
        if self._scf._eri is not None:
            eri = pyscf.ao2mo.incore.full(self._scf._eri, mo)
        elif nao*nao*nmo*nmo/4*8/1e6 > self.max_memory:
            ftmp = tempfile.NamedTemporaryFile()
            pyscf.ao2mo.outcore.full(self.mol, mo, ftmp.name,
                                     verbose=self.verbose)
            #pyscf.ao2mo.direct.full(self.mol, mo, ftmp.name, \
            #                        max_memory=self.max_memory, verbose=self.verbose)
            with h5py.File(ftmp.name, 'r') as feri:
                eri = numpy.array(feri['eri_mo'])
        else:
            eri = pyscf.ao2mo.direct.full_iofree(self.mol, mo,
                                                 verbose=self.verbose)
        return eri

    def casci(self, mo=None, ci0=None):
        if mo is None:
            mo = self.mo_coeff
        if ci0 is None:
            ci0 = self.ci

        self.mol.check_sanity(self)

        self.dump_flags()

        self.e_tot, e_cas, self.ci = \
                kernel(self, mo, ci0=ci0, verbose=self.verbose)
        return self.e_tot, e_cas, self.ci



if __name__ == '__main__':
    import gto
    import scf
    mol = gto.Mole()
    mol.verbose = 0
    mol.output = None#"out_h2o"
    mol.atom = [
        ['O', ( 0., 0.    , 0.   )],
        ['H', ( 0., -0.757, 0.587)],
        ['H', ( 0., 0.757 , 0.587)],]

    mol.basis = {'H': 'sto-3g',
                 'O': '6-31g',}
    mol.build()

    m = scf.RHF(mol)
    ehf = m.scf()
    mc = CASCI(mol, m, 4, 4)
    emc = mc.casci()[0] + mol.nuclear_repulsion()
    print(ehf, emc, emc-ehf)
    #-75.9577817425 -75.9624554777 -0.00467373522233
    print(emc+75.9624554777)

    mc = CASCI(mol, m, 4, (3,1))
    mc.fcisolver = pyscf.fci.direct_spin1
    emc = mc.casci()[0] + mol.nuclear_repulsion()
    print(emc - -75.439016172976)

    mol = gto.Mole()
    mol.verbose = 0
    mol.output = "out_casci"
    mol.atom = [
        ["C", (-0.65830719,  0.61123287, -0.00800148)],
        ["C", ( 0.73685281,  0.61123287, -0.00800148)],
        ["C", ( 1.43439081,  1.81898387, -0.00800148)],
        ["C", ( 0.73673681,  3.02749287, -0.00920048)],
        ["C", (-0.65808819,  3.02741487, -0.00967948)],
        ["C", (-1.35568919,  1.81920887, -0.00868348)],
        ["H", (-1.20806619, -0.34108413, -0.00755148)],
        ["H", ( 1.28636081, -0.34128013, -0.00668648)],
        ["H", ( 2.53407081,  1.81906387, -0.00736748)],
        ["H", ( 1.28693681,  3.97963587, -0.00925948)],
        ["H", (-1.20821019,  3.97969587, -0.01063248)],
        ["H", (-2.45529319,  1.81939187, -0.00886348)],]

    mol.basis = {'H': 'sto-3g',
                 'C': 'sto-3g',}
    mol.build()

    m = scf.RHF(mol)
    ehf = m.scf()
    mc = CASCI(mol, m, 9, 8)
    emc = mc.casci()[0] + mol.nuclear_repulsion()
    print(ehf, emc, emc-ehf)
    print(emc - -227.948912536)

    mc = CASCI(mol, m, 9, (5,3))
    mc.fcisolver = pyscf.fci.direct_spin1
    emc = mc.casci()[0] + mol.nuclear_repulsion()
    print(emc - -227.7674519720)
