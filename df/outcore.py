#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

import time
import ctypes
import _ctypes
import tempfile
import numpy
import scipy.linalg
import h5py
import pyscf.lib
from pyscf.lib import logger
import pyscf.gto
from pyscf.ao2mo import _ao2mo
from pyscf.scf import _vhf
from pyscf.df import incore

#
# for auxe1 (ij|P)
#

libri = pyscf.lib.load_library('libri')
def _fpointer(name):
    return ctypes.c_void_p(_ctypes.dlsym(libri._handle, name))

def cholesky_eri(mol, erifile, auxbasis='weigend', dataname='eri_mo', tmpdir=None,
                 int3c='cint3c2e_sph', aosym='s2ij', int2c='cint2c2e_sph', comp=1,
                 ioblk_size=256, verbose=0):
    '''3-center 2-electron AO integrals
    '''
    assert(aosym in ('s1', 's2ij'))
    assert(comp == 1)
    time0 = (time.clock(), time.time())
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mol.stdout, verbose)

    swapfile = tempfile.NamedTemporaryFile(dir=tmpdir)
    cholesky_eri_b(mol, swapfile.name, auxbasis, dataname,
                   int3c, aosym, int2c, comp, ioblk_size, verbose=log)
    fswap = h5py.File(swapfile.name, 'r')
    time1 = log.timer('generate (ij|L) 1 pass', *time0)

    nao = mol.nao_nr()
    auxmol = incore.format_aux_basis(mol, auxbasis)
    naoaux = auxmol.nao_nr()
    aosym = _stand_sym_code(aosym)
    if aosym == 's1':
        nao_pair = nao * nao
    else:
        nao_pair = nao * (nao+1) // 2

    if h5py.is_hdf5(erifile):
        feri = h5py.File(erifile)
        if dataname in feri:
            del(feri[dataname])
    else:
        feri = h5py.File(erifile, 'w')
    if comp == 1:
        chunks = (min(int(16e3/nao),naoaux), nao) # 128K
        h5d_eri = feri.create_dataset(dataname, (naoaux,nao_pair), 'f8',
                                      chunks=chunks)
        aopairblks = len(fswap[dataname])
    else:
        chunks = (1, min(int(16e3/nao),naoaux), nao) # 128K
        h5d_eri = feri.create_dataset(dataname, (comp,naoaux,nao_pair), 'f8',
                                      chunks=chunks)
        aopairblks = len(fswap[dataname+'/0'])
    if comp > 1:
        for icomp in range(comp):
            feri.create_group(str(icomp)) # for h5py old version

    iolen = min(int(ioblk_size*1e6/8/nao_pair), naoaux)
    totstep = (naoaux+iolen-1)//iolen * comp
    buf = numpy.empty((iolen, nao_pair))
    istep = 0
    ti0 = time1
    for icomp in range(comp):
        for row0, row1 in prange(0, naoaux, iolen):
            nrow = row1 - row0
            istep += 1

            col0 = 0
            for ic in range(aopairblks):
                if comp == 1:
                    dat = fswap['%s/%d'%(dataname,ic)]
                else:
                    dat = fswap['%s/%d/%d'%(dataname,icomp,ic)]
                col1 = col0 + dat.shape[1]
                buf[:nrow,col0:col1] = dat[row0:row1]
                col0 = col1
            if comp == 1:
                h5d_eri[row0:row1] = buf[:nrow]
            else:
                h5d_eri[icomp,row0:row1] = buf[:nrow]
            ti0 = log.timer('step 2 [%d/%d], [%d,%d:%d], row = %d'%
                            (istep, totstep, icomp, row0, row1, nrow), *ti0)

    fswap.close()
    feri.close()
    log.timer('cholesky_eri', *time0)
    return erifile

# store cderi in blocks
def cholesky_eri_b(mol, erifile, auxbasis='weigend', dataname='eri_mo',
                   int3c='cint3c2e_sph', aosym='s2ij', int2c='cint2c2e_sph',
                   comp=1, ioblk_size=256, verbose=logger.NOTE):
    '''3-center 2-electron AO integrals
    '''
    assert(aosym in ('s1', 's2ij'))
    assert(comp == 1)
    time0 = (time.clock(), time.time())
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mol.stdout, verbose)
    auxmol = incore.format_aux_basis(mol, auxbasis)
    j2c = incore.fill_2c2e(mol, auxmol, intor=int2c)
    log.debug('size of aux basis %d', j2c.shape[0])
    time1 = log.timer('2c2e', *time0)
    low = scipy.linalg.cholesky(j2c, lower=True)
    j2c = None
    time1 = log.timer('Cholesky 2c2e', *time1)

    if h5py.is_hdf5(erifile):
        feri = h5py.File(erifile)
        if dataname in feri:
            del(feri[dataname])
    else:
        feri = h5py.File(erifile, 'w')
    if comp > 1:
        for icomp in range(comp):
            feri.create_group(str(icomp)) # for h5py old version

    nao = mol.nao_nr()
    naoaux = auxmol.nao_nr()
    if aosym == 's1':
        fill = _fpointer('RIfill_s1_auxe2')
        nao_pair = nao * nao
        buflen = min(max(int(ioblk_size*1e6/8/naoaux/comp), 1), nao_pair)
        shranges = _guess_shell_ranges(mol, buflen, 's1')
    else:
        fill = _fpointer('RIfill_s2ij_auxe2')
        nao_pair = nao * (nao+1) // 2
        buflen = min(max(int(ioblk_size*1e6/8/naoaux/comp), 1), nao_pair)
        shranges = _guess_shell_ranges(mol, buflen, 's2ij')
    log.debug('erifile %.8g MB, IO buf size %.8g MB',
              naoaux*nao_pair*8/1e6, comp*buflen*naoaux*8/1e6)
    log.debug1('shranges = %s', shranges)

    atm, bas, env = \
            pyscf.gto.mole.conc_env(mol._atm, mol._bas, mol._env,
                                    auxmol._atm, auxmol._bas, auxmol._env)
    c_atm = numpy.array(atm, dtype=numpy.int32)
    c_bas = numpy.array(bas, dtype=numpy.int32)
    c_env = numpy.array(env)
    natm = ctypes.c_int(mol.natm+auxmol.natm)
    nbas = ctypes.c_int(mol.nbas)
    fintor = _fpointer(int3c)
    cintopt = _vhf.make_cintopt(c_atm, c_bas, c_env, int3c)
    for istep, sh_range in enumerate(shranges):
        log.debug('int3c2e [%d/%d], AO [%d:%d], nrow = %d', \
                  istep+1, len(shranges), *sh_range)
        bstart, bend, nrow = sh_range
        buf = numpy.empty((comp,nrow,naoaux))
        libri.RInr_3c2e_auxe2_drv(fintor, fill,
                                  buf.ctypes.data_as(ctypes.c_void_p),
                                  ctypes.c_int(bstart), ctypes.c_int(bend-bstart),
                                  ctypes.c_int(mol.nbas), ctypes.c_int(auxmol.nbas),
                                  ctypes.c_int(comp), cintopt,
                                  c_atm.ctypes.data_as(ctypes.c_void_p), natm,
                                  c_bas.ctypes.data_as(ctypes.c_void_p), nbas,
                                  c_env.ctypes.data_as(ctypes.c_void_p))
        for icomp in range(comp):
            if comp == 1:
                label = '%s/%d'%(dataname,istep)
            else:
                label = '%s/%d/%d'%(dataname,icomp,istep)
            cderi = scipy.linalg.solve_triangular(low, buf[icomp].T,
                                                  lower=True, overwrite_b=True)
            feri[label] = cderi
        time1 = log.timer('gen CD eri [%d/%d]' % (istep+1,len(shranges)), *time1)

    feri.close()
    libri.CINTdel_optimizer(ctypes.byref(cintopt))
    return erifile


def general(mol, mo_coeffs, erifile, auxbasis='weigend', dataname='eri_mo', tmpdir=None,
            int3c='cint3c2e_sph', aosym='s2ij', int2c='cint2c2e_sph', comp=1,
            max_memory=2000, ioblk_size=256, verbose=0, compact=True):
    ''' Transform ij of (ij|L) to MOs.
    '''
    assert(aosym in ('s1', 's2ij'))
    assert(comp == 1)
    time0 = (time.clock(), time.time())
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mol.stdout, verbose)

    swapfile = tempfile.NamedTemporaryFile(dir=tmpdir)
    cholesky_eri_b(mol, swapfile.name, auxbasis, dataname,
                   int3c, aosym, int2c, comp, ioblk_size, verbose=log)
    fswap = h5py.File(swapfile.name, 'r')
    time1 = log.timer('AO->MO eri transformation 1 pass', *time0)

    ijsame = compact and iden_coeffs(mo_coeffs[0], mo_coeffs[1])
    nmoi = mo_coeffs[0].shape[1]
    nmoj = mo_coeffs[1].shape[1]
    nao = mo_coeffs[0].shape[0]
    auxmol = incore.format_aux_basis(mol, auxbasis)
    naoaux = auxmol.nao_nr()
    aosym = _stand_sym_code(aosym)
    if aosym == 's1':
        nao_pair = nao * nao
        aosym_as_nr_e2 = 's1'
    else:
        nao_pair = nao * (nao+1) // 2
        aosym_as_nr_e2 = 's2kl'

    if compact and ijsame and aosym != 's1':
        log.debug('i-mo == j-mo')
        ijmosym = 's2'
        nij_pair = nmoi*(nmoi+1) // 2
        moij = numpy.asarray(mo_coeffs[0], order='F')
        ijshape = (0, nmoi, 0, nmoi)
    else:
        ijmosym = 's1'
        nij_pair = nmoi*nmoj
        moij = numpy.asarray(numpy.hstack((mo_coeffs[0],mo_coeffs[1])), order='F')
        ijshape = (0, nmoi, nmoi, nmoj)

    if h5py.is_hdf5(erifile):
        feri = h5py.File(erifile)
        if dataname in feri:
            del(feri[dataname])
    else:
        feri = h5py.File(erifile, 'w')
    if comp == 1:
        chunks = (min(int(16e3/nmoj),naoaux), nmoj) # 128K
        h5d_eri = feri.create_dataset(dataname, (naoaux,nij_pair), 'f8',
                                      chunks=chunks)
        aopairblks = len(fswap[dataname])
    else:
        chunks = (1, min(int(16e3/nmoj),naoaux), nmoj) # 128K
        h5d_eri = feri.create_dataset(dataname, (comp,naoaux,nij_pair), 'f8',
                                      chunks=chunks)
        aopairblks = len(fswap[dataname+'/0'])
    if comp > 1:
        for icomp in range(comp):
            feri.create_group(str(icomp)) # for h5py old version

    iolen = min(int(ioblk_size*1e6/8/(nao_pair+nij_pair)), naoaux)
    totstep = (naoaux+iolen-1)//iolen * comp
    buf = numpy.empty((iolen, nao_pair))
    istep = 0
    ti0 = time1
    for icomp in range(comp):
        for row0, row1 in prange(0, naoaux, iolen):
            nrow = row1 - row0
            istep += 1

            log.debug('step 2 [%d/%d], [%d,%d:%d], row = %d',
                      istep, totstep, icomp, row0, row1, nrow)
            col0 = 0
            for ic in range(aopairblks):
                if comp == 1:
                    dat = fswap['%s/%d'%(dataname,ic)]
                else:
                    dat = fswap['%s/%d/%d'%(dataname,icomp,ic)]
                col1 = col0 + dat.shape[1]
                buf[:nrow,col0:col1] = dat[row0:row1]
                col0 = col1

            buf1 = _ao2mo.nr_e2_(buf[:nrow], moij, ijshape, aosym_as_nr_e2, ijmosym)
            if comp == 1:
                h5d_eri[row0:row1] = buf1
            else:
                h5d_eri[icomp,row0:row1] = buf1

            ti0 = log.timer('step 2 [%d/%d], [%d,%d:%d], row = %d'%
                            (istep, totstep, icomp, row0, row1, nrow), *ti0)

    fswap.close()
    feri.close()
    log.timer('AO->MO CD eri transformation 2 pass', *time1)
    log.timer('AO->MO CD eri transformation', *time0)
    return erifile


def iden_coeffs(mo1, mo2):
    return (id(mo1) == id(mo2)) \
            or (mo1.shape==mo2.shape and numpy.allclose(mo1,mo2))

def prange(start, end, step):
    for i in range(start, end, step):
        yield i, min(i+step, end)

def _guess_shell_ranges(mol, buflen, aosym):
    bas_dim = [(mol.bas_angular(i)*2+1)*(mol.bas_nctr(i)) \
               for i in range(mol.nbas)]
    ao_loc = [0]
    for i in bas_dim:
        ao_loc.append(ao_loc[-1]+i)
    nao = ao_loc[-1]

    ish_seg = [0] # record the starting shell id of each buffer
    bufrows = []
    ij_start = 0

    if aosym in ('s2ij'):
        for i in range(mol.nbas):
            ij_end = ao_loc[i+1]*(ao_loc[i+1]+1)//2
            if ij_end - ij_start > buflen and i != 0:
                ish_seg.append(i) # put present shell to next segments
                ijend = ao_loc[i]*(ao_loc[i]+1)//2
                bufrows.append(ijend-ij_start)
                ij_start = ijend
        nao_pair = nao*(nao+1) // 2
        ish_seg.append(mol.nbas)
        bufrows.append(nao_pair-ij_start)
    else:
        for i in range(mol.nbas):
            ij_end = ao_loc[i+1] * nao
            if ij_end - ij_start > buflen and i != 0:
                ish_seg.append(i) # put present shell to next segments
                ijend = ao_loc[i] * nao
                bufrows.append(ijend-ij_start)
                ij_start = ijend
        ish_seg.append(mol.nbas)
        bufrows.append(nao*nao-ij_start)

    # for each buffer, sh_ranges record (start, end, bufrow)
    sh_ranges = list(zip(ish_seg[:-1], ish_seg[1:], bufrows))
    return sh_ranges

def _stand_sym_code(sym):
    if isinstance(sym, int):
        return 's%d' % sym
    elif 's' == sym[0] or 'a' == sym[0]:
        return sym
    else:
        return 's' + sym


if __name__ == '__main__':
    mol = pyscf.gto.Mole()
    mol.verbose = 0
    mol.output = None

    mol.atom = [
        ["O" , (0. , 0.     , 0.)],
        [1   , (0. , -0.757 , 0.587)],
        [1   , (0. , 0.757  , 0.587)]]

    mol.basis = 'cc-pvtz'
    mol.build()

    cderi0 = incore.cholesky_eri(mol)
    cholesky_eri(mol, 'cderi.dat')
    with h5py.File('cderi.dat') as feri:
        print(numpy.allclose(feri['eri_mo'], cderi0))

    cholesky_eri(mol, 'cderi.dat', ioblk_size=.5)
    with h5py.File('cderi.dat') as feri:
        print(numpy.allclose(feri['eri_mo'], cderi0))

    general(mol, (numpy.eye(mol.nao_nr()),)*2, 'cderi.dat',
            max_memory=.5, ioblk_size=.2, verbose=6)
    with h5py.File('cderi.dat') as feri:
        print(numpy.allclose(feri['eri_mo'], cderi0))
