#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
Non-relativistic
'''

import time
import numpy
from pyscf.lib import logger
from pyscf.scf import _vhf


def grad_elec(grad_mf, mo_energy=None, mo_coeff=None, mo_occ=None, atmlst=None):
    mf = grad_mf._scf
    mol = grad_mf.mol
    if mo_energy is None: mo_energy = mf.mo_energy
    if mo_occ is None:    mo_occ = mf.mo_occ
    if mo_coeff is None:  mo_coeff = mf.mo_coeff
    log = logger.Logger(grad_mf.stdout, grad_mf.verbose)

    h1 = grad_mf.get_hcore(mol)
    s1 = grad_mf.get_ovlp(mol)
    dm0 = mf.make_rdm1(mf.mo_coeff, mf.mo_occ)

    t0 = (time.clock(), time.time())
    log.debug('Compute Gradients of NR Hartree-Fock Coulomb repulsion')
    vhf = grad_mf.get_veff(mol, dm0)
    log.timer('gradients of 2e part', *t0)

    f1 = h1 + vhf
    dme0 = grad_mf.make_rdm1e(mf.mo_energy, mf.mo_coeff, mf.mo_occ)

    if atmlst is None:
        atmlst = range(mol.natm)
    offsetdic = grad_mf.aorange_by_atom()
    de = numpy.zeros((len(atmlst),3))
    for k, ia in enumerate(atmlst):
        shl0, shl1, p0, p1 = offsetdic[ia]
# h1, s1, vhf are \nabla <i|h|j>, the nuclear gradients = -\nabla
        vrinv = grad_mf._grad_rinv(mol, ia)
        de[k] += numpy.einsum('xij,ij->x', f1[:,p0:p1], dm0[p0:p1]) * 2
        de[k] += numpy.einsum('xij,ij->x', vrinv, dm0) * 2
        de[k] -= numpy.einsum('xij,ij->x', s1[:,p0:p1], dme0[p0:p1]) * 2
    log.debug('gradients of electronic part')
    log.debug(str(de))
    return de

def grad_nuc(mol, atmlst=None):
    gs = numpy.zeros((mol.natm,3))
    for j in range(mol.natm):
        q2 = mol.atom_charge(j)
        r2 = mol.atom_coord(j)
        for i in range(mol.natm):
            if i != j:
                q1 = mol.atom_charge(i)
                r1 = mol.atom_coord(i)
                r = numpy.sqrt(numpy.dot(r1-r2,r1-r2))
                gs[j] -= q1 * q2 * (r2-r1) / r**3
    if atmlst is not None:
        gs = gs[atmlst]
    return gs


def get_hcore(mol):
    h =(mol.intor('cint1e_ipkin_sph', comp=3)
      + mol.intor('cint1e_ipnuc_sph', comp=3))
    return -h

def get_ovlp(mol):
    return -mol.intor('cint1e_ipovlp_sph', comp=3)

def get_veff(mol, dm):
    return get_coulomb_hf(mol, dm)
def get_coulomb_hf(mol, dm):
    '''NR Hartree-Fock Coulomb repulsion'''
    #vj, vk = pyscf.scf.hf.get_vj_vk(pycint.nr_vhf_grad_o1, mol, dm)
    #return vj - vk*.5
    vj, vk = _vhf.direct_mapdm('cint2e_ip1_sph',  # (nabla i,j|k,l)
                               's2kl', # ip1_sph has k>=l,
                               ('lk->s1ij', 'jk->s1il'),
                               dm, 3, # xyz, 3 components
                               mol._atm, mol._bas, mol._env)
    return -(vj - vk*.5)

def make_rdm1e(mo_energy, mo_coeff, mo_occ):
    '''Energy weighted density matrix'''
    mo0 = mo_coeff[:,mo_occ>0]
    mo0e = mo0 * (mo_energy[mo_occ>0] * mo_occ[mo_occ>0])
    return numpy.dot(mo0e, mo0.T.conj())

def aorange_by_atom(mol):
    aorange = []
    p0 = p1 = 0
    b0 = b1 = 0
    ia0 = 0
    for ib in range(mol.nbas):
        if ia0 != mol.bas_atom(ib):
            aorange.append((b0, ib, p0, p1))
            ia0 = mol.bas_atom(ib)
            p0 = p1
            b0 = ib
        p1 += (mol.bas_angular(ib)*2+1) * mol.bas_nctr(ib)
    aorange.append((b0, mol.nbas, p0, p1))
    return aorange


class RHF(object):
    '''Non-relativistic restricted Hartree-Fock gradients'''
    def __init__(self, scf_method):
        self.verbose = scf_method.verbose
        self.stdout = scf_method.stdout
        self.mol = scf_method.mol
        self._scf = scf_method
        self.chkfile = scf_method.chkfile

    def dump_flags(self):
        pass
#        log.info(self, '\n')
#        log.info(self, '******** Gradients flags ********')
#        if not self._scf.converged:
#            log.warn(self, 'underneath SCF of gradients not converged')
#        log.info(self, '\n')

    def get_hcore(self, mol=None):
        if mol is None: mol = self.mol
        return get_hcore(mol)

    def get_ovlp(self, mol=None):
        if mol is None: mol = self.mol
        return get_ovlp(mol)

    def get_veff(self, mol=None, dm=None):
        if mol is None: mol = self.mol
        if dm is None: dm = self._scf.make_rdm1()
        return get_coulomb_hf(mol, dm)

    def make_rdm1e(self, mo_energy=None, mo_coeff=None, mo_occ=None):
        if mo_energy is None: mo_energy = self._scf.mo_energy
        if mo_coeff is None: mo_coeff = self._scf.mo_coeff
        if mo_occ is None: mo_occ = self._scf.mo_occ
        return make_rdm1e(mo_energy, mo_coeff, mo_occ)

    def _grad_rinv(self, mol, ia):
        r''' for given atom, <|\nabla r^{-1}|> '''
        mol.set_rinv_origin_(mol.atom_coord(ia))
        return -mol.atom_charge(ia) * mol.intor('cint1e_iprinv_sph', comp=3)

    def grad_elec(self, mo_energy=None, mo_coeff=None, mo_occ=None,
                  atmlst=None):
        if mo_energy is None: mo_energy = self._scf.mo_energy
        if mo_coeff is None: mo_coeff = self._scf.mo_coeff
        if mo_occ is None: mo_occ = self._scf.mo_occ
        return grad_elec(self, mo_energy, mo_coeff, mo_occ, atmlst)

    def grad_nuc(self, mol=None, atmlst=None):
        if mol is None: mol = self.mol
        return grad_nuc(mol, atmlst)

    def kernel(self, mo_energy=None, mo_coeff=None, mo_occ=None, atmlst=None):
        return self.grad(mo_energy, mo_coeff, mo_occ, atmlst)
    def grad(self, mo_energy=None, mo_coeff=None, mo_occ=None, atmlst=None):
        cput0 = (time.clock(), time.time())
        if mo_energy is None: mo_energy = self._scf.mo_energy
        if mo_coeff is None: mo_coeff = self._scf.mo_coeff
        if mo_occ is None: mo_occ = self._scf.mo_occ
        if self.verbose >= logger.INFO:
            self.dump_flags()
        de =(self.grad_elec(mo_energy, mo_coeff, mo_occ, atmlst)
           + self.grad_nuc(atmlst))
        logger.note(self, 'HF gradinets')
        logger.note(self, '==============')
        logger.note(self, '           x                y                z')
        if atmlst is None:
            atmlst = range(self.mol.natm)
        for k, ia in enumerate(atmlst):
            logger.note(self, '%d %s  %15.9f  %15.9f  %15.9f', ia,
                        self.mol.atom_symbol(ia), de[k,0], de[k,1], de[k,2])
        logger.timer(self, 'HF gradients', *cput0)
        return de

    def aorange_by_atom(self):
        return aorange_by_atom(self.mol)


if __name__ == '__main__':
    from pyscf import gto
    from pyscf import scf
    mol = gto.Mole()
    mol.verbose = 0
    mol.output = None
    mol.atom = [['He', (0.,0.,0.)], ]
    mol.basis = {'He': 'ccpvdz'}
    mol.build()
    method = scf.RHF(mol)
    method.scf()
    g = RHF(method)
    print(g.grad())

    h2o = gto.Mole()
    h2o.verbose = 0
    h2o.output = None#'out_h2o'
    h2o.atom = [
        ['O' , (0. , 0.     , 0.)],
        [1   , (0. , -0.757 , 0.587)],
        [1   , (0. , 0.757  , 0.587)] ]
    h2o.basis = {'H': '631g',
                 'O': '631g',}
    h2o.build()
    rhf = scf.RHF(h2o)
    rhf.scf()
    g = RHF(rhf)
    print(g.grad())
#[[ 0   0               -2.41134256e-02]
# [ 0   4.39690522e-03   1.20567128e-02]
# [ 0  -4.39690522e-03   1.20567128e-02]]

