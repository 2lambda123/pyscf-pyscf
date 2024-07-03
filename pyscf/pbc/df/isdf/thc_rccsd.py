#!/usr/bin/env python
# Copyright 2014-2020 The PySCF Developers. All Rights Reserved.
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
# Author: Ning Zhang <ningzhang1024@gmail.com>
#

import numpy
import pyscf
from pyscf import lib
from pyscf import ao2mo
from pyscf.ao2mo.incore import iden_coeffs
from pyscf.pbc import tools
from pyscf.pbc.lib import kpts_helper
from pyscf.lib import logger
from pyscf.pbc.lib.kpts_helper import is_zero, gamma_point, unique
from pyscf import __config__
from pyscf.pbc.df.fft_ao2mo import _format_kpts, _iskconserv, _contract_compact
import pyscf.pbc.gto as pbcgto

import numpy as np
import ctypes
libpbc = lib.load_library('libpbc')

from pyscf.pbc.df.isdf.isdf_jk import _benchmark_time
import pyscf.pbc.df.isdf.isdf_linear_scaling as ISDF
from pyscf.pbc.df.isdf.isdf_tools_cell import build_supercell, build_supercell_with_partition
from pyscf.pbc.df.isdf.isdf_ao2mo import LS_THC, LS_THC_eri, laplace_holder

from pyscf.pbc.df.isdf.isdf_posthf import _restricted_THC_posthf_holder 

from pyscf.pbc.df.isdf.isdf_tools_mpi import rank, comm, comm_size, bcast
from pyscf.pbc.df.isdf.thc_einsum     import thc_einsum, energy_denomimator, thc_holder
from pyscf.mp.mp2                     import MP2


from pyscf.cc import ccsd
from pyscf import __config__
import pyscf.pbc.df.isdf.thc_cc_helper._thc_rccsd as _thc_rccsd_ind
import pyscf.pbc.df.isdf.thc_cc_helper._einsum_holder as einsum_holder

####### TORCH BACKEND #######

FOUND_TORCH = False
GPU_SUPPORTED = False

try:
    import torch
    FOUND_TORCH = True
except ImportError:
    pass

if FOUND_TORCH:
    if torch.cuda.is_available():
        GPU_SUPPORTED = True

##############################

class THC_RCCSD(ccsd.CCSD, _restricted_THC_posthf_holder):
    
    def __init__(self, 
                 my_mf=None, 
                 frozen=None, mo_coeff=None, mo_occ=None,
                 my_isdf=None, 
                 X=None, ## used in XXZXX
                 laplace_rela_err = 1e-7,
                 laplace_order    = 2,
                 no_LS_THC        = False,
                 backend = "opt_einsum",
                 memory=2**28,
                 use_torch=False,
                 with_gpu =False):
        
        #### initialization ####
        
        if my_isdf is None:
            assert my_mf is not None
            my_isdf = my_mf.with_df
        
        _restricted_THC_posthf_holder.__init__(self, my_isdf, my_mf, X,
                                                laplace_rela_err = laplace_rela_err,
                                                laplace_order    = laplace_order,
                                                no_LS_THC        = no_LS_THC,
                                                use_torch        = use_torch,
                                                with_gpu         = with_gpu)
    
        ccsd.CCSD.__init__(self, my_mf, frozen, mo_coeff, mo_occ)
        
        if hasattr(self, "level_shift"):
            assert self.level_shift == 0.0 or self.level_shift is None
        
        self._backend = backend
        self._memory  = memory
        
        self.nthc = self.X_o.shape[1]
        
        #self.diis = False ## should scaled to the same scale as t1 
        
        #self._use_torch = use_torch
        #self._with_gpu  = with_gpu
        
        #### init projector ####
        
        self._thc_eris = self.ao2mo(self.mo_coeff)
        
        proj = self.init_projector()
        
        #self.init_mp2_projector()
        
        #### init amps #### 
        
        time0 = logger.process_clock(), logger.perf_counter()
        _, t1, thc_t2 = self.init_amps(self._thc_eris)
        logger.timer(self, 'init_amps', *time0)
        
        self.t1, self.t2 = t1, thc_t2
        
        #### init scheduler #### 
        
        time0 = logger.process_clock(), logger.perf_counter()
        
        self._thc_scheduler = einsum_holder.THC_scheduler(
            X_O=self.X_o, X_V=self.X_v, THC_INT=self.Z,
            T1=t1, 
            XO_T2=self.X_o, XV_T2=self.X_v, THC_T2=thc_t2,
            TAU_O=self.tau_o, TAU_V=self.tau_v,
            PROJECTOR=proj,
            use_torch=use_torch,
            with_gpu=with_gpu
        )
        
        ### build exprs ###
        
        t1_expr = einsum_holder._expr_t1()
        t2_expr = einsum_holder._expr_t2()
        t2_expr = t2_expr.transpose((0,2,1,3))
        _thc_rccsd_ind.update_amps(self, t1_expr, t2_expr, self._thc_eris, self._thc_scheduler)
        _thc_rccsd_ind.energy(self, t1_expr, t2_expr, self._thc_eris, self._thc_scheduler)
        self._thc_scheduler._build_expression()
        self._thc_scheduler._build_contraction(backend=backend, optimize=True)
        
        logger.timer(self, 'build thc ccsd scheduler', *time0)
    
    def ao2mo(self, mo_coeff=None):
        return _make_eris_incore(self, mo_coeff)

    def init_projector(self): ### THC projector
        
        time0 = logger.process_clock(), logger.perf_counter()
        X_o = _thc_rccsd_ind._tensor_to_cpu(self.X_o)
        X_v = _thc_rccsd_ind._tensor_to_cpu(self.X_v)
        PROJ_INV = np.einsum('iP,aP,iQ,aQ->PQ', X_o, X_v, X_o, X_v, optimize=True)
        import scipy
        D_RR, U_RR = scipy.linalg.eigh(PROJ_INV)
        kept = D_RR > D_RR[-1]*1e-14
        
        print("condition number = ", D_RR[-1]/D_RR[0])    
        print("nkept = ", np.sum(kept))
        
        D_RR     = D_RR[kept]
        U_RR     = U_RR[:,kept]
        D_RR_inv = (1.0/D_RR).copy()
        PROJ     = U_RR @ np.diag(D_RR_inv) @ U_RR.T
        
        #logger.timer(self, 'build projector', *time1)
        
        self.projector = PROJ
        
        D_RR_sqrt     = np.sqrt(D_RR)
        D_RR_inv_sqrt = 1.0/D_RR_sqrt
        
        self.projector_sqrt     = U_RR @ np.diag(D_RR_inv_sqrt) @ U_RR.T
        self.projector_inv_sqrt = U_RR @ np.diag(D_RR_sqrt) @ U_RR.T
        
        logger.timer(self, 'build thc projector', *time0)
        
        return PROJ

    def init_mp2_projector(self, mp2_cutoff=1e-3, df_obj=None):
        
        time0 = logger.process_clock(), logger.perf_counter()
        
        #X_o = _thc_rccsd_ind._tensor_to_cpu(self.X_o)
        #X_v = _thc_rccsd_ind._tensor_to_cpu(self.X_v)
        tau_o = _thc_rccsd_ind._tensor_to_cpu(self.tau_o)
        tau_v = _thc_rccsd_ind._tensor_to_cpu(self.tau_v)
        
        ### not positive definite, abandon this scheme ! 
        # Z   = _thc_rccsd_ind._tensor_to_cpu(self.Z)
        # D_Z, U_Z = np.linalg.eigh(Z)
        # print(D_Z)
        # kept = D_Z > D_Z[-1]*1e-14  ## check whether it is positive definite
        # U_Z = U_Z[:, kept]
        # D_Z = D_Z[kept]
        
        # build L_ia^C 
        
        if df_obj is None:
            if hasattr(self, "df_obj"):
                df_obj = self.df_obj
            else:
                ### build one with default settings ###
                
                from pyscf.pbc import scf
                
                time1 = logger.process_clock(), logger.perf_counter()
                _mf = scf.RHF(self.mol).density_fit()
                _mf.with_df.build() # build 
                df_obj = _mf.with_df
                logger.timer(self, 'build df obj', *time1)
        
        naux = df_obj.get_naoaux()
        nmo  = self.mo_coeff.shape[1]
        nocc = self.nocc
        nvir = nmo - nocc
        nlaplace = self.n_laplace
        mo = numpy.asarray(self.mo_coeff, order='F')
        
        print("naux ", naux)
        print("nmo  ", nmo)
        print("nocc ", nocc)
        print("nvir ", nvir)
        
        L_cW_ia = np.zeros((naux, nlaplace, nocc, nvir))
        
        ijslice = (0, nmo, 0, nmo)
        p1 = 0
        Lpq = None
        for k, eri1 in enumerate(df_obj.loop()):
            from pyscf.ao2mo import _ao2mo
            Lpq = _ao2mo.nr_e2(eri1, mo, ijslice, aosym='s2', mosym='s1', out=Lpq)
            p0, p1 = p1, p1 + Lpq.shape[0]
            Lpq = Lpq.reshape(p1-p0,nmo,nmo)
            #Loo[p0:p1] = Lpq[:,:nocc,:nocc]
            Lov = Lpq[:,:nocc,nocc:]
            #Lvv = lib.pack_tril(Lpq[:,nocc:,nocc:])
            #eris.vvL[:,p0:p1] = Lvv.T
            L_cW_ia[p0:p1,:,:,:] = np.einsum("iW,aW,Cia->CWia", tau_o, tau_v, Lov, optimize=True)
        
        M_cW_cW = np.einsum("CWia,DXia->CWDX", L_cW_ia, L_cW_ia, optimize=True)
        M_cW_cW = M_cW_cW.reshape(nlaplace*naux, nlaplace*naux)
        TAU_V, V_CW_V = np.linalg.eigh(M_cW_cW)
        kept = TAU_V > TAU_V[-1]*mp2_cutoff
        print("condition number = ", TAU_V[-1]/TAU_V[0])
        print("nkept = ", np.sum(kept))
        #print("TAU_V = ", TAU_V)
        exit(1)
        
        logger.timer(self, 'build mp2 projector', *time0)

        #exit(1)

    def init_amps(self, eris=None):
        
        time0 = logger.process_clock(), logger.perf_counter()
        if eris is None:
            eris = self.ao2mo(self.mo_coeff)
        e_hf = self.e_hf
        if e_hf is None: e_hf = self.get_e_hf(mo_coeff=self.mo_coeff)
        mo_e = eris.mo_energy
        nocc = self.nocc
        nvir = mo_e.size - nocc
        eia = mo_e[:nocc,None] - mo_e[None,nocc:]

        t1 = eris.fock[:nocc,nocc:] / eia
        
        #t2 = numpy.empty((nocc,nocc,nvir,nvir), dtype=eris.ovov.dtype)
        #max_memory = self.max_memory - lib.current_memory()[0]
        #blksize = int(min(nvir, max(BLKMIN, max_memory*.3e6/8/(nocc**2*nvir+1))))
        #emp2 = 0
        #for p0, p1 in lib.prange(0, nvir, blksize):
        #    eris_ovov = eris.ovov[:,p0:p1]
        #    t2[:,:,p0:p1] = (eris_ovov.transpose(0,2,1,3).conj()
        #                     / lib.direct_sum('ia,jb->ijab', eia[:,p0:p1], eia))
        #    emp2 += 2 * numpy.einsum('ijab,iajb', t2[:,:,p0:p1], eris_ovov)
        #    emp2 -=     numpy.einsum('jiab,iajb', t2[:,:,p0:p1], eris_ovov)
        
        #t2_thc = np.einsum('AP,iP,aP,iR,aR,RS,jS,bS,jQ,bQ,QB,iT,jT,aT,bT->AB', 
        #                   self.projector, self.X_o, self.X_v, 
        #                   self.X_o, self.X_v, self.Z, self.X_o, self.X_v, 
        #                   self.X_o, self.X_v, self.projector,
        #                   self.tau_o, self.tau_o, self.tau_v, self.tau_v,
        #                   optimize=True)
        
        thc_eri  = thc_holder(self.X_o, self.X_v, self.Z)
        ene_deno = energy_denomimator(self.tau_o, self.tau_v)
        
        #ene_full = np.einsum("iT,jT,aT,bT->ijab", self.tau_o, self.tau_o, self.tau_v, self.tau_v, optimize=True)
        #eri_full = np.einsum("iP,aP,PQ,jQ,bQ->iajb", self.X_o, self.X_v, self.Z, self.X_o, self.X_v, optimize=True)
        #print("eri_full", eri_full[0,0,0,:])
        #print("ene_full", ene_full[0,0,0,:])
        #print("self.projector", self.projector)
        #e_mp2_benchmark = 0
        #e_mp2_benchmark -= 2 * np.einsum("iajb,iajb,ijab->", eri_full, eri_full, ene_full, optimize=True)
        #e_mp2_benchmark += np.einsum("iajb,ibja,ijab->", eri_full, eri_full, ene_full, optimize=True)
        #print("e_mp2_benchmark", e_mp2_benchmark)
        
        #t2_thc_path = thc_einsum('AP,iP,aP,iajb,ijab,jQ,bQ,QB->AB', self.projector, self.X_o, self.X_v, thc_eri, 
        #                    ene_deno, self.X_o, self.X_v, self.projector, optimize=True, backend=self._backend, memory=self._memory
        #                    , return_path_only=True
        #                    )
        #print(t2_thc_path[1])
        
        if self._with_gpu:
            self.projector = einsum_holder._to_torch_tensor(self.projector).to("cuda")
        
        t2_thc = -thc_einsum('AP,iP,aP,iajb,ijab,jQ,bQ,QB->AB', self.projector, self.X_o, self.X_v, thc_eri, 
                            ene_deno, self.X_o, self.X_v, self.projector, optimize=True, backend=self._backend, memory=self._memory
                            #, return_path_only=True
                            )
        
        #t2_thc_benchmark = np.einsum('AP,iP,aP,iajb,ijab,jQ,bQ,QB->AB', self.projector, self.X_o, self.X_v, eri_full,
        #                                ene_full, self.X_o, self.X_v, self.projector, optimize=True)
        #print("t2_thc_benchmark", t2_thc_benchmark[0,:])
        #print("t2_thc", t2_thc[0,:])
        #diff = np.linalg.norm(t2_thc - t2_thc_benchmark)
        #print("diff = ", diff)
        #assert np.allclose(t2_thc, t2_thc_benchmark)
        #print(t2_thc_path)
        #exit(1)
        
        t2_holder = thc_holder(self.X_o, self.X_v, t2_thc)
        
        emp2  = 0
        emp2 += 2 * thc_einsum('iajb,iajb->', t2_holder, thc_eri, optimize=True, backend=self._backend, memory=self._memory)
        emp2 -=     thc_einsum('iajb,ibja->', t2_holder, thc_eri, optimize=True, backend=self._backend, memory=self._memory)
        
        self.emp2 = emp2.real

        logger.info(self, 'Init t2, MP2 energy = %.15g  E_corr(MP2) %.15g',
                    e_hf + self.emp2, self.emp2)
        logger.timer(self, 'init mp2', *time0)
        
        return self.emp2, t1, t2_thc

    def amplitudes_to_vector(self, t1, t2, out=None):
        #if not isinstance(t1, numpy.ndarray):
        #    t1 = np.asarray(t1)
        #if not isinstance(t2, numpy.ndarray):
        #    t2 = np.asarray(t2)
        assert t1 is not None and t2 is not None
        t1 = _thc_rccsd_ind._tensor_to_cpu(t1)
        t2 = _thc_rccsd_ind._tensor_to_cpu(t2)
        nocc, nvir = t1.shape
        nov = nocc * nvir
        nthc = self.nthc
        size = nov + nthc * (nthc+1) // 2
        vector = numpy.ndarray(size, t1.dtype, buffer=out)
        vector[:nov] = t1.ravel()
        from functools import reduce
        t2_scaled = reduce(numpy.dot, (self.projector_inv_sqrt, t2, self.projector_inv_sqrt.T))
        lib.pack_tril(t2_scaled.reshape(nthc,nthc), out=vector[nov:])
        return vector
    
    def vector_to_amplitudes(self, vec, nmo=None, nocc=None):
        if nocc is None: nocc = self.nocc
        if nmo is None: nmo = self.nmo
        nvir = nmo - nocc
        nov  = nocc * nvir
        nthc = self.nthc
        t1 = vec[:nov].copy().reshape((nocc,nvir))
        t2 = lib.unpack_tril(vec[nov:], filltriu=lib.SYMMETRIC)
        from functools import reduce
        t2 = reduce(numpy.dot, (self.projector_sqrt.T, t2, self.projector_sqrt))
        return t1, numpy.asarray(t2, order='C')
            

    def vector_size(self, nmo=None, nocc=None):
        if nocc is None: nocc = self.nocc
        if nmo is None: nmo = self.nmo
        nvir = nmo - nocc
        nov = nocc * nvir
        nthc = self.nthc
        return nov + nthc * (nthc+1) // 2

    ### rewrite energy and update_amps ###
    
    def energy(self, t1=None, t2=None, eris=None):
        if t1 is None: t1 = self.t1
        if t2 is None: t2 = self.t2
        #return _thc_rccsd_ind.energy(self, t1, t2, self._thc_eris, self._thc_scheduler)
        return self._thc_scheduler.energy(t1, t2)

    def update_amps(self, t1, t2, eris):
        
        if self.cc2:
            raise NotImplementedError

        #print("t1", t1[0,:])
        #print("t2", t2[0,:])

        _, t1_new, t2_new = self._thc_scheduler.evaluate_t1_t2(t1, t2, evaluate_ene=False)
        
        #print("t1_new", t1_new[0,:])
        #print("t2_new", t2_new[0,:])
        
        return t1_new, t2_new

    def kernel(self, t1=None, t2=None, eris=None, mbpt2=False):
        if mbpt2 == False:
            raise NotImplementedError
        return self.ccsd(t1, t2, eris, mbpt2)
    
    def ccsd(self, t1=None, t2=None, eris=None, mbpt2=False):
        '''Ground-state CCSD.

        Kwargs:
            mbpt2 : bool
                Use one-shot MBPT2 approximation to CCSD.
        '''
        if mbpt2:
            #pt = mp2.MP2(self._scf, self.frozen, self.mo_coeff, self.mo_occ)
            #self.e_corr, self.t2 = pt.kernel(eris=eris)
            #nocc, nvir = self.t2.shape[1:3]
            #self.t1 = np.zeros((nocc,nvir))
            #return self.e_corr, self.t1, self.t2
            raise NotImplementedError

        if eris is None:
            eris = self.ao2mo(self.mo_coeff)
        return ccsd.CCSDBase.ccsd(self, t1, t2, eris)

################## ERIS ##################

from pyscf.cc import ccsd

class _THC_ERIs(ccsd._ChemistsERIs):
    
    def __init__(self, mol=None):
        super().__init__(mol)
                
        #eri exprs
        
        self.ovvo = einsum_holder._thc_eri_ovvo()
        self.oovv = einsum_holder._thc_eri_oovv()
        self.ovov = einsum_holder._thc_eri_ovov()
        self.ovoo = einsum_holder._thc_eri_ovoo()
        self.ovvv = einsum_holder._thc_eri_ovvv()
        self.oooo = einsum_holder._thc_eri_oooo()
        self.vvvv = einsum_holder._thc_eri_vvvv()

def _make_eris_incore(mycc, mo_coeff=None, ao2mofn=None):
    cput0 = (logger.process_clock(), logger.perf_counter())
    eris = _THC_ERIs()
    eris._common_init_(mycc, mo_coeff)
    eris.nvir = nvir = eris.mo_energy[0].size - mycc.nocc
    logger.timer(mycc, 'CCSD integral transformation', *cput0)
    return eris
        
        
#### test ####

if __name__ == "__main__":
    
    c = 15
    N = 1
    
    #if rank == 0:
    cell   = pbcgto.Cell()
    boxlen = 3.5668
    cell.a = np.array([[boxlen,0.0,0.0],[0.0,boxlen,0.0],[0.0,0.0,boxlen]]) 
    
    cell.atom = [
                ['C', (0.     , 0.     , 0.    )],
                ['C', (0.8917 , 0.8917 , 0.8917)],
                ['C', (1.7834 , 1.7834 , 0.    )],
                ['C', (2.6751 , 2.6751 , 0.8917)],
                ['C', (1.7834 , 0.     , 1.7834)],
                ['C', (2.6751 , 0.8917 , 2.6751)],
                ['C', (0.     , 1.7834 , 1.7834)],
                ['C', (0.8917 , 2.6751 , 2.6751)],
            ] 

    cell.basis   = 'gth-szv'
    #cell.basis = 'gth-cc-dzvp'
    cell.pseudo  = 'gth-pade'
    cell.verbose = 10
    cell.ke_cutoff = 70
    cell.max_memory = 800  # 800 Mb
    cell.precision  = 1e-8  # integral precision
    cell.use_particle_mesh_ewald = True 
    
    verbose = 10
    
    prim_cell = build_supercell(cell.atom, cell.a, Ls = [1,1,1], ke_cutoff=cell.ke_cutoff, basis=cell.basis, pseudo=cell.pseudo, verbose=verbose)   
    prim_partition = [[0,1,2,3], [4,5,6,7]]
    #prim_partition=  [[0,1]]
    prim_mesh = prim_cell.mesh
    
    Ls = [1, 1, N]
    Ls = np.array(Ls, dtype=np.int32)
    mesh = [Ls[0] * prim_mesh[0], Ls[1] * prim_mesh[1], Ls[2] * prim_mesh[2]]
    mesh = np.array(mesh, dtype=np.int32)
    
    cell, group_partition = build_supercell_with_partition(
                                    cell.atom, cell.a, mesh=mesh, 
                                    Ls=Ls,
                                    basis=cell.basis, 
                                    pseudo=cell.pseudo,
                                    partition=prim_partition, ke_cutoff=cell.ke_cutoff, verbose=verbose) 
    
    ####### isdf MP2 can perform directly! ####### 
    
    import numpy
    from pyscf.pbc import gto, scf, mp    
    
    myisdf = ISDF.PBC_ISDF_Info_Quad(cell, with_robust_fitting=True, aoR_cutoff=1e-8, direct=False, use_occ_RI_K=False)
    myisdf.build_IP_local(c=c, m=5, group=group_partition, Ls=[Ls[0]*10, Ls[1]*10, Ls[2]*10])
    myisdf.build_auxiliary_Coulomb(debug=True)
            
    mf_isdf           = scf.RHF(cell)
    myisdf.direct_scf = mf_isdf.direct_scf
    mf_isdf.with_df   = myisdf
    mf_isdf.max_cycle = 8
    mf_isdf.conv_tol  = 1e-8
    mf_isdf.kernel()
    
    ####### thc rmp2 #######
    
    X = myisdf.aoRg_full()
    
    thc_ccsd = THC_RCCSD(my_mf=mf_isdf, X=X, memory=2**31, backend="opt_einsum", use_torch=True, with_gpu=True)
    
    thc_ccsd.ccsd()
    
    #thc_rmp2 = THC_RMP2(my_mf=mf_isdf, X=X)
    #e_mp2, _ = thc_rmp2.kernel(backend='cotengra')
    #print("ISDF MP2 energy", e_mp2)
    #e_mp2, _ = thc_rmp2.kernel(backend="opt_einsum")
    #print("ISDF MP2 energy", e_mp2)        

    from pyscf.pbc.df.isdf.thc_rmp2 import THC_RMP2
    thc_rmp2 = THC_RMP2(my_mf=mf_isdf, X=X)
    e_mp2, _ = thc_rmp2.kernel(backend="opt_einsum")
    print("ISDF MP2 energy", e_mp2)