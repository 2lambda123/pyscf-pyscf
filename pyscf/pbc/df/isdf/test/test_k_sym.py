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

import copy
from functools import reduce
import numpy as np
from pyscf import lib
import pyscf.pbc.gto as pbcgto
from pyscf.pbc.gto import Cell
from pyscf.pbc import tools
from pyscf.pbc.lib.kpts import KPoints
from pyscf.pbc.lib.kpts_helper import is_zero, gamma_point, member
from pyscf.gto.mole import *
from pyscf.pbc.df.isdf.isdf_jk import _benchmark_time
import pyscf.pbc.df.isdf.isdf_ao2mo as isdf_ao2mo
import pyscf.pbc.df.isdf.isdf_jk as isdf_jk

import ctypes

from multiprocessing import Pool

from memory_profiler import profile

libpbc = lib.load_library('libpbc')

BASIS_CUTOFF               = 1e-18  # too small may lead to numerical instability
CRITERION_CALL_PARALLEL_QR = 256

from pyscf.pbc.df.isdf.isdf_eval_gto import ISDF_eval_gto

def _extract_grid_primitive_cell(cell_a, mesh, Ls, coords):
    """
    Extract the primitive cell grid information from the supercell grid information
    """
    
    print("In _extract_grid_primitive_cell")
    
    assert cell_a[0, 1] == 0.0
    assert cell_a[0, 2] == 0.0
    assert cell_a[1, 0] == 0.0
    assert cell_a[1, 2] == 0.0
    assert cell_a[2, 0] == 0.0
    assert cell_a[2, 1] == 0.0
    
    ngrids = np.prod(mesh)
    print("ngrids = ", ngrids)

    assert ngrids == coords.shape[0]
    
    Lx = Ls[0]
    Ly = Ls[1]
    Lz = Ls[2]
    
    print("Lx = ", Lx)
    print("Ly = ", Ly)
    print("Lz = ", Lz)
    
    print("Length supercell x = %15.6f , primitive cell x = %15.6f" % (cell_a[0, 0], cell_a[0, 0] / Lx))
    print("Length supercell y = %15.6f , primitive cell y = %15.6f" % (cell_a[1, 1], cell_a[1, 1] / Ly))
    print("Length supercell z = %15.6f , primitive cell z = %15.6f" % (cell_a[2, 2], cell_a[2, 2] / Lz))
    
    nx, ny, nz = mesh
    
    print("nx = ", nx)
    print("ny = ", ny)
    print("nz = ", nz)
    
    coords = coords.reshape(nx, ny, nz, 3)
    
    assert nx % Lx == 0
    assert ny % Ly == 0
    assert nz % Lz == 0
    
    nx_prim = nx // Lx
    ny_prim = ny // Ly
    nz_prim = nz // Lz
    
    print("nx_prim = ", nx_prim)
    print("ny_prim = ", ny_prim)
    print("nz_prim = ", nz_prim)
    
    ngrids_prim = nx_prim * ny_prim * nz_prim
    
    res_dict = {}
    
    res = []
        
    prim_grid = coords[:nx_prim, :ny_prim, :nz_prim].reshape(-1, 3)
        
    for ix in range(Lx):
        for iy in range(Ly):
            for iz in range(Lz):
                x_0 = ix * nx_prim
                x_1 = (ix + 1) * nx_prim
                y_0 = iy * ny_prim
                y_1 = (iy + 1) * ny_prim
                z_0 = iz * nz_prim
                z_1 = (iz + 1) * nz_prim
                
                grid_tmp = coords[x_0:x_1, y_0:y_1, z_0:z_1].reshape(-1, 3)
                
                shift_bench = np.zeros((3), dtype=np.float64)
                shift_bench[0] = ix * cell_a[0, 0] / Lx
                shift_bench[1] = iy * cell_a[1, 1] / Ly
                shift_bench[2] = iz * cell_a[2, 2] / Lz
                
                shifts = grid_tmp - prim_grid
                
                # print("shifts = ", shifts)
                print("shift_bench = ", shift_bench)
                
                for ID in range(shifts.shape[0]):
                    shift = shifts[ID]
                    # print("shift = ", shift)
                    if np.allclose(shift, shift_bench) == False:
                        tmp = shift - shift_bench
                        nx = round (tmp[0] / cell_a[0, 0])
                        ny = round (tmp[1] / cell_a[1, 1])
                        nz = round (tmp[2] / cell_a[2, 2])
                        # print(tmp)
                        # print(nx, ny, nz)
                        assert np.allclose(tmp[0], nx * cell_a[0, 0])
                        assert np.allclose(tmp[1], ny * cell_a[1, 1])
                        assert np.allclose(tmp[2], nz * cell_a[2, 2])
                        # grid_tmp[ID] = prim_grid[ID] + shift_bench, do not shift to avoid numerical error

                res.append(grid_tmp)
                res_dict[(nx, ny, nz)] = grid_tmp
    
    return res, res_dict
                
# the following subroutine are all testing functions

def _RowCol_FFT_ColFull_bench(input, Ls, mesh):
    """
    A is a 3D array, (nbra, nket, ngrid_prim)
    """
    A = input
    ncell = np.prod(Ls)
    nGrids = np.prod(mesh)
    assert A.shape[1] == nGrids
    assert A.shape[0] % ncell == 0
    A = A.reshape(A.shape[0], *mesh)
    # perform 3d fft 
    A = np.fft.fftn(A, axes=(1, 2, 3))
    A = A.reshape(A.shape[0], -1)
    print("finish transform ket")
    # transform bra
    NPOINT_BRA = A.shape[0] // ncell
    A = A.reshape(-1, NPOINT_BRA, A.shape[1])
    A = A.transpose(1, 2, 0)
    shape_tmp = A.shape
    A = A.reshape(-1, *Ls)
    A = np.fft.ifftn(A, axes=(1, 2, 3))
    A = A.reshape(shape_tmp)
    A = A.transpose(2, 0, 1)
    A = A.reshape(-1, A.shape[2])
    print("finish transform bra")
    return A

def _RowCol_FFT_bench(input, Ls, inv=False, TransBra = True, TransKet = True):
    """
    A is a 3D array, (nbra, nket, ngrid_prim)
    """
    A = input
    ncell = np.prod(Ls)
    assert A.shape[1] % ncell == 0
    assert A.shape[0] % ncell == 0
    NPOINT_KET = A.shape[1] // ncell
    if TransKet:
        A = A.reshape(A.shape[0], -1, NPOINT_KET) # nbra, nBox, NPOINT
        A = A.transpose(0, 2, 1)                  # nbra, NPOINT, nBox
        shape_tmp = A.shape
        A = A.reshape(A.shape[0] * NPOINT_KET, *Ls)
        # perform 3d fft 
        if inv:
            A = np.fft.ifftn(A, axes=(1, 2, 3))
        else:
            A = np.fft.fftn(A, axes=(1, 2, 3))
        A = A.reshape(shape_tmp)
        A = A.transpose(0, 2, 1)
        A = A.reshape(A.shape[0], -1)
        print("finish transform ket")
    # transform bra
    NPOINT_BRA = A.shape[0] // ncell
    if TransBra:
        A = A.reshape(-1, NPOINT_BRA, A.shape[1])
        A = A.transpose(1, 2, 0)
        shape_tmp = A.shape
        A = A.reshape(-1, *Ls)
        if inv:
            A = np.fft.fftn(A, axes=(1, 2, 3))
        else:
            A = np.fft.ifftn(A, axes=(1, 2, 3))
        A = A.reshape(shape_tmp)
        A = A.transpose(2, 0, 1)
        A = A.reshape(-1, A.shape[2])
        print("finish transform bra")
    # print(A[:NPOINT, :NPOINT])
    return A

def _RowCol_FFT_Fast(input, Ls):
    A = input
    ncell = np.prod(Ls)
    assert A.shape[1] % ncell == 0
    
    NPOINT_KET = A.shape[1] // ncell
    A = A.reshape(A.shape[0], -1, NPOINT_KET) # nbra, nBox, NPOINT
    A = A.transpose(0, 2, 1)                  # nbra, NPOINT, nBox
    shape_tmp = A.shape
    A = A.reshape(A.shape[0] * NPOINT_KET, *Ls)
    # perform 3d fft
    A = np.fft.fftn(A, axes=(1, 2, 3))
    A = A.reshape(shape_tmp)
    A = A.transpose(0, 2, 1)
    A = A.reshape(A.shape[0], -1)
    
    return A

def _RowCol_rFFT_Fast(input, Ls, inv=False):
    A = input
    if inv:
        Ls_now = [Ls[0], Ls[1], Ls[2]//2+1]
        ncell = np.prod([Ls[0], Ls[1], Ls[2]//2+1])
    else:
        Ls_now = Ls
        ncell = np.prod(Ls)
    assert A.shape[1] % ncell == 0
    
    NPOINT_KET = A.shape[1] // ncell
    A = A.reshape(A.shape[0], -1, NPOINT_KET) # nbra, nBox, NPOINT
    A = A.transpose(0, 2, 1)                  # nbra, NPOINT, nBox
    shape_tmp = A.shape
    A = A.reshape(A.shape[0] * NPOINT_KET, *Ls_now)
    # perform 3d fft
    # print(A.shape)
    # print(A)
    if inv:
        assert A.dtype == np.complex128
        A = np.fft.irfftn(A, axes=(1, 2, 3), s=Ls) # the input is real
        nReal = np.prod(Ls)
        A = A.reshape((shape_tmp[0], shape_tmp[1], nReal))
    else:
        assert A.dtype == np.float64
        A = np.fft.rfftn(A, axes=(1, 2, 3)) # the input is real 
    # print(A.shape)
    # print(A)
        nComplex = np.prod([Ls[0], Ls[1], Ls[2]//2+1])
        A = A.reshape((shape_tmp[0], shape_tmp[1], nComplex))
    A = A.transpose(0, 2, 1)
    A = A.reshape(A.shape[0], -1)
    
    # exit(1)
    return A

if __name__ == '__main__':

    ############ PREPARING DATA ############

    cell   = pbcgto.Cell()
    boxlen = 3.5668
    cell.a = np.array([[boxlen,0.0,0.0],[0.0,boxlen,0.0],[0.0,0.0,boxlen]])

    cell.atom = '''
                   C     0.      0.      0.
                   C     0.8917  0.8917  0.8917
                   C     1.7834  1.7834  0.
                   C     2.6751  2.6751  0.8917
                   C     1.7834  0.      1.7834
                   C     2.6751  0.8917  2.6751
                   C     0.      1.7834  1.7834
                   C     0.8917  2.6751  2.6751
                '''

    cell.basis   = 'gth-dzvp'
    # cell.basis   = 'gth-tzvp'
    cell.pseudo  = 'gth-pade'
    cell.verbose = 4

    # cell.ke_cutoff  = 256   # kinetic energy cutoff in a.u.
    cell.ke_cutoff = 4
    cell.max_memory = 800  # 800 Mb
    cell.precision  = 1e-8  # integral precision
    cell.use_particle_mesh_ewald = True

    cell.build()

    Ls   = [2, 3, 5]
    cell = tools.super_cell(cell, Ls)

    from pyscf.pbc.dft.multigrid.multigrid_pair import MultiGridFFTDF2, _eval_rhoG

    df_tmp = MultiGridFFTDF2(cell)

    grids  = df_tmp.grids
    coords = np.asarray(grids.coords).reshape(-1,3)
    nx = grids.mesh[0]

    # for i in range(coords.shape[0]):
    #     print(coords[i])
    # exit(1)

    mesh   = grids.mesh
    ngrids = np.prod(mesh)
    assert ngrids == coords.shape[0]
    
    print("ngrids = ", ngrids)
    print("mesh   = ", mesh)
    print("cell.a = ", cell.a)
    
    grid_primitive, _ = _extract_grid_primitive_cell(cell.a, mesh, Ls, coords) 
    
    ngrid_prim = grid_primitive[0].size // 3
    print(ngrid_prim)
    
    NPOINT = 8
    
    # generate NPOINT random ints (no repeated) from [0, ngrid_prim)
    
    idx = np.random.choice(ngrid_prim, NPOINT, replace=False)
    idx.sort()
    print(idx)
    coord_ordered = []
    IP_coord      = []
    for i in range(len(grid_primitive)):
        IP_coord.append(grid_primitive[i][idx])
        coord_ordered.append(grid_primitive[i])
    IP_coord = np.asarray(IP_coord)
    coord_ordered = np.asarray(coord_ordered)
    print(IP_coord.shape)
    print(coord_ordered.shape)
    IP_coord = IP_coord.reshape(-1,3)
    coord_ordered = coord_ordered.reshape(-1,3)
    
    # calculate aoRg 
    aoRg   = df_tmp._numint.eval_ao(cell, IP_coord)[0].T  # the T is important
    aoRg  *= np.sqrt(cell.vol / ngrids)
    aoR    = df_tmp._numint.eval_ao(cell, coord_ordered)[0].T
    aoR   *= np.sqrt(cell.vol / ngrids)
    
    ################ TEST WHETHER C^\dagger AC  and C^\dagger B D is block diagonal ################
    
    A = np.asarray(lib.dot(aoRg.T, aoRg), order='C')
    print(A.shape)
    A = A ** 2
    
    print(A.shape)
    ncell = np.prod(Ls)
    
    B = np.asarray(lib.dot(aoRg.T, aoR), order='C')
    B = B ** 2
    
    A = _RowCol_FFT_bench(A, Ls)
    B = _RowCol_FFT_bench(B, Ls)
    
    # print(A)
    # print(B)
    
    # assert np.allclose(A.imag, 0.0)
    # assert np.allclose(B.imag, 0.0)
    
    # exit(1)
    
    for i in range(ncell):
        b_begin = i * NPOINT
        b_end   = (i + 1) * NPOINT
        k_begin = i * NPOINT
        k_end   = (i + 1) * NPOINT
        mat     = A[b_begin:b_end, k_begin:k_end]

        assert np.allclose(mat, mat.T.conj()) # block diagonal， A is Hermitian Conjugate and positive definite

        mat_before = A[b_begin:b_end, :k_begin]
        assert np.allclose(mat_before, 0.0)  # block diagonal 
        mat_after  = A[b_begin:b_end, k_end:]
        assert np.allclose(mat_after, 0.0)   # block diagonal 
    
        # test B 
        
        k_begin = i * ngrid_prim
        k_end   = (i + 1) * ngrid_prim
        mat     = B[b_begin:b_end, k_begin:k_end]
        
        mat_before = B[b_begin:b_end, :k_begin]
        assert np.allclose(mat_before, 0.0) # block diagonal
        mat_after = B[b_begin:b_end, k_end:]
        assert np.allclose(mat_after, 0.0)  # block diagonal
    
    ################ Test a much more efficient way to construct A, B , only half FFT is needed ################
    
    A_test = np.zeros_like(A)
    B_test = np.zeros_like(B)
        
    A2 = np.asarray(lib.dot(aoRg.T, aoRg), order='C')
    A2 = A2 ** 2
    
    A_tmp = A2[:NPOINT, :].copy()
    A_tmp = _RowCol_FFT_Fast(A_tmp, Ls)
    A_tmp_rFFT = A2[:NPOINT, :].copy()
    A_tmp_rFFT = _RowCol_rFFT_Fast(A_tmp_rFFT, Ls)
    
    B2 = np.asarray(lib.dot(aoRg.T, aoR), order='C')
    B2 = B2 ** 2
    
    B_tmp = B2[:NPOINT, :].copy()
    B_tmp = _RowCol_FFT_Fast(B_tmp, Ls)
    B_tmp_rFFT = B2[:NPOINT, :].copy()
    B_tmp_rFFT = _RowCol_rFFT_Fast(B_tmp_rFFT, Ls)
    
    # diagonal block
    
    A_Diag = []
    B_Diag = []
    X_Diag_bench = np.zeros_like(B_tmp_rFFT)
    X_Diag_bench_full = np.zeros_like(B_tmp)
    
    for i in range(ncell):

        b_begin = i * NPOINT
        b_end   = (i + 1) * NPOINT
        k_begin = i * NPOINT
        k_end   = (i + 1) * NPOINT
        matA     = A_tmp[:, k_begin:k_end]
        assert np.allclose(matA, A[b_begin:b_end, k_begin:k_end])
        
        A_Diag.append(matA)
        
        k_begin = i * ngrid_prim
        k_end   = (i + 1) * ngrid_prim
        matB     = B_tmp[:, k_begin:k_end]
        assert np.allclose(matB, B[b_begin:b_end, k_begin:k_end])
    
        B_Diag.append(matB)
        
        X_Diag_bench_full[:,k_begin:k_end] = np.linalg.solve(matA, matB)

        X_tmp = np.linalg.solve(matA, matB)
        print("cell %d" % i)
        print(matA)
        print(matB)
        # print(X_tmp[:2,:2])
        print(X_tmp)
    
    ncell_complex = np.prod([Ls[0], Ls[1], Ls[2]//2+1])
    
    for i in range(ncell_complex):

        k_begin = i * NPOINT
        k_end   = (i + 1) * NPOINT
        Amat     = A_tmp_rFFT[:, k_begin:k_end] # must be square
        assert np.allclose(Amat.T.conj(), Amat)
        k_begin = i * ngrid_prim
        k_end   = (i + 1) * ngrid_prim
        Bmat     = B_tmp_rFFT[:, k_begin:k_end]
        
        X = np.linalg.solve(Amat, Bmat)
        X_Diag_bench[:, k_begin:k_end] = X
            
    ################################ test rFFT, both python and C ################################
    
    B2 = np.asarray(lib.dot(aoRg.T, aoR), order='C')
    B2 = B2 ** 2
    
    B_tmp2 = B2[:NPOINT, :].copy()

    mesh_complex = np.asarray([Ls[0], Ls[1], Ls[2]//2+1], dtype=np.int32)
    mesh_real = np.asarray(Ls, dtype=np.int32)
    nMeshReal = np.prod(Ls)
    nMeshComplex = np.prod(mesh_complex)
    assert B_tmp2.shape[1] % nMeshReal == 0
    nPoint = B_tmp2.shape[1] // nMeshReal
    
    Matrix = np.zeros((B_tmp2.shape[0], nPoint, nMeshComplex), dtype=np.complex128, order='C')
    Matrix_real = np.ndarray((B_tmp2.shape[0], nPoint, nMeshReal), dtype=np.float64, order='C', buffer=Matrix)
    Matrix_real.ravel()[:] = B_tmp2.ravel()
    
    Buf = np.zeros((B_tmp2.shape[0], nPoint, nMeshComplex), dtype=np.complex128, order='C')
    
    fn = getattr(libpbc, "_FFT_Matrix_Col_InPlace", None)
    assert fn is not None
    
    fn(
        Matrix_real.ctypes.data_as(ctypes.c_void_p),
        ctypes.c_int(B_tmp.shape[0]),
        ctypes.c_int(nPoint),
        mesh_real.ctypes.data_as(ctypes.c_void_p),
        Buf.ctypes.data_as(ctypes.c_void_p)
    )

    B_tmp2 = _RowCol_rFFT_Fast(B_tmp2, Ls)
    assert np.allclose(Matrix.ravel(), B_tmp2.ravel()) # we get the correct answer!!! 
    
    ## test the inv FFT 
    
    B_tmp2 = _RowCol_rFFT_Fast(B_tmp2, Ls, inv=True)
    
    fn2 = getattr(libpbc, "_iFFT_Matrix_Col_InPlace", None)
    assert fn2 is not None
    
    fn2(
        Matrix.ctypes.data_as(ctypes.c_void_p),
        ctypes.c_int(B_tmp2.shape[0]),
        ctypes.c_int(nPoint),
        mesh_real.ctypes.data_as(ctypes.c_void_p),
        Buf.ctypes.data_as(ctypes.c_void_p)
    )
    
    Matrix_real = Matrix_real.reshape(Matrix_real.shape[0], -1)
    assert np.allclose(Matrix_real, B_tmp2) # we get the correct answer!!!

    ################################ solve AX=B ################################
    
    fn_cholesky = getattr(libpbc, "Complex_Cholesky", None)
    assert fn_cholesky is not None
    fn_solve = getattr(libpbc, "Solve_LLTEqualB_Complex_Parallel", None)
    assert fn_solve is not None
    
    for i in range(ncell):
        A = A_Diag[i].copy()
        B = B_Diag[i].copy()
        X = np.linalg.solve(A, B)
        
        fn_cholesky(
            A.ctypes.data_as(ctypes.c_void_p),
            ctypes.c_int(A.shape[0])
        )
        
        nrhs = B.shape[1]
        num_threads = lib.num_threads()
        bunchsize = nrhs // num_threads
        
        fn_solve(
            ctypes.c_int(A.shape[0]),
            A.ctypes.data_as(ctypes.c_void_p),
            B.ctypes.data_as(ctypes.c_void_p),
            ctypes.c_int(B.shape[1]),
            ctypes.c_int(bunchsize)
        )
        
        assert np.allclose(X, B)
    
    ################################ check the structure of X ################################
    
    A = np.asarray(lib.dot(aoRg.T, aoRg), order='C')
    A = A ** 2
    B = np.asarray(lib.dot(aoRg.T, aoR), order='C')
    B = B ** 2
    
    # benchmark 
    
    X = np.linalg.solve(A, B)    
    
    # another way to solve AX=B
    
    A2 = _RowCol_FFT_bench(A, Ls)
    B2 = _RowCol_FFT_bench(B, Ls)
    X2 = np.linalg.solve(A2, B2)
    X3 = X2.copy()
    X2 = _RowCol_FFT_bench(X2, Ls, inv=True)
    X2_imag = X2.imag
    X2 = X2.real
    assert np.allclose(X, X2)
    assert np.allclose(X2_imag, 0.0)
    assert np.allclose(X2, X) # we get the correct answer!!!
    
    X3_bench = _RowCol_FFT_bench(X, Ls, TransKet=False)
    X3_bench = X3_bench[:NPOINT, :]
    X3_bench_imag = X3_bench.imag
    X3_bench = X3_bench.real
    assert np.allclose(X3_bench_imag, 0.0) 
    
    X3 = _RowCol_FFT_bench(X3, Ls, inv=True, TransBra=False) # to test fast case 
    
    X3_first_RowBlock = X3[:NPOINT, :].copy()
    X3_first_RowBlock_imag = X3_first_RowBlock.imag
    X3_first_RowBlock = X3_first_RowBlock.real
    assert np.allclose(X3_first_RowBlock_imag, 0.0) ### X3 first block, i.e. the ref block must be real! 
    assert np.allclose(X3_first_RowBlock, X3_bench) # we get the correct answer!!!
        
    X3_0 = X3_bench[:, :ngrid_prim]
    X3_1 = X3_bench[:, ngrid_prim:2*ngrid_prim]
    assert np.allclose(X3_0, X3_1) # X3_0 and X3_1 must be the same
        
    ### test fast case 
    
    A_tmp = A[:NPOINT, :].copy()
    B_tmp = B[:NPOINT, :].copy()
    
    nPoint_A = A_tmp.shape[1] // nMeshReal
    Matrix_A = np.zeros((A_tmp.shape[0], nPoint_A, nMeshComplex), dtype=np.complex128, order='C')
    Matrix_A_real = np.ndarray((A_tmp.shape[0], nPoint_A, nMeshReal), dtype=np.float64, order='C', buffer=Matrix_A)
    Matrix_A_real.ravel()[:] = A_tmp.ravel()
    
    Buf_A = np.zeros((A_tmp.shape[0], nPoint_A, nMeshComplex), dtype=np.complex128, order='C')
    
    fn(
        Matrix_A_real.ctypes.data_as(ctypes.c_void_p),
        ctypes.c_int(A_tmp.shape[0]),
        ctypes.c_int(nPoint_A),
        mesh_real.ctypes.data_as(ctypes.c_void_p),
        Buf_A.ctypes.data_as(ctypes.c_void_p)
    )
    
    Matrix_A = Matrix_A.reshape(Matrix_A.shape[0], -1)

    assert np.allclose(Matrix_A, A_tmp_rFFT)
    
    nPoint_B = B_tmp.shape[1] // nMeshReal
    Matrix_B = np.zeros((B_tmp.shape[0], nPoint_B, nMeshComplex), dtype=np.complex128, order='C')
    Matrix_B_real = np.ndarray((B_tmp.shape[0], nPoint_B, nMeshReal), dtype=np.float64, order='C', buffer=Matrix_B)
    Matrix_B_real.ravel()[:] = B_tmp.ravel()
    
    Buf_B = np.zeros((B_tmp.shape[0], nPoint_B, nMeshComplex), dtype=np.complex128, order='C')
    
    fn(
        Matrix_B_real.ctypes.data_as(ctypes.c_void_p),
        ctypes.c_int(B_tmp.shape[0]),
        ctypes.c_int(nPoint_B),
        mesh_real.ctypes.data_as(ctypes.c_void_p),
        Buf_B.ctypes.data_as(ctypes.c_void_p)
    )
    
    Matrix_B = Matrix_B.reshape(Matrix_B.shape[0], -1)
    
    assert np.allclose(Matrix_B, B_tmp_rFFT)
    
    # solve block diagonal case
    
    X4 = np.zeros_like(Matrix_B)
    
    ncell_complex = np.prod([Ls[0], Ls[1], Ls[2]//2+1])
    
    print("npoint_A = ", nPoint_A)
    print("npoint_B = ", nPoint_B)
    
    for i in range(ncell_complex):

        # print("Compr cell %d" % i)
        
        # A_tmp = Matrix_A[:, i * nPoint_A:(i + 1) * nPoint_A].copy()
        # B_tmp = Matrix_B[:, i * nPoint_B:(i + 1) * nPoint_B].copy()
        
        A_tmp = np.zeros((nPoint_A, nPoint_A), dtype=np.complex128, order='C')
        B_tmp = np.zeros((nPoint_A, nPoint_B), dtype=np.complex128, order='C')
        A_tmp.ravel()[:] = Matrix_A[:, i * nPoint_A:(i + 1) * nPoint_A].ravel()
        B_tmp.ravel()[:] = Matrix_B[:, i * nPoint_B:(i + 1) * nPoint_B].ravel()
        
        fn_cholesky(
            A_tmp.ctypes.data_as(ctypes.c_void_p),
            ctypes.c_int(A_tmp.shape[0])
        )
        
        nrhs = B_tmp.shape[1]
        num_threads = lib.num_threads()
        bunchsize = nrhs // num_threads
        
        fn_solve(
            ctypes.c_int(A_tmp.shape[0]),
            A_tmp.ctypes.data_as(ctypes.c_void_p),
            B_tmp.ctypes.data_as(ctypes.c_void_p),
            ctypes.c_int(B_tmp.shape[1]),
            ctypes.c_int(bunchsize)
        )
        
        X4[:, i * nPoint_B:(i + 1) * nPoint_B] = B_tmp
    
    assert np.allclose(X4, X_Diag_bench)  # we get the correct answer!!!
    
    ####### check the structure of X after FFT ####### 
    
    print(coords)
    aoR_unordered    = df_tmp._numint.eval_ao(cell, coords)[0].T
    aoR_unordered   *= np.sqrt(cell.vol / ngrids)
    
    A = np.asarray(lib.dot(aoRg.T, aoRg), order='C')
    A = A ** 2
    B_unordered = np.asarray(lib.dot(aoRg.T, aoR_unordered), order='C')
    B_unordered = B_unordered ** 2
    
    X_unorder = np.linalg.solve(A, B_unordered)
    print("Ls   = ", Ls)
    print("mesh = ", mesh)
    X_unorder = _RowCol_FFT_ColFull_bench(X_unorder, Ls, mesh)
    
    nBox = [mesh[0]//Ls[0], mesh[1]//Ls[1], mesh[2]//Ls[2]] 
    
    X_unorder = X_unorder.reshape(-1, nBox[0], Ls[0], nBox[1],Ls[1], nBox[2], Ls[2])
    X_unorder = X_unorder.transpose(0, 2, 4, 6, 1, 3, 5)
    X_unorder = X_unorder.reshape(-1, np.prod(mesh))
    
    def _check_X_symmetry(input_X, Ls, mesh):
        assert input_X.shape[1] == np.prod(mesh)
        ncell = np.prod(Ls)
        NPOINT_per_cell = input_X.shape[0] // ncell
        ngrid_prim = input_X.shape[1] // ncell
                
        print("ncell = ", ncell)
        print("NPOINT_per_cell = ", NPOINT_per_cell)
        print("ngrid_prim = ", ngrid_prim)
                
        for i in range(ncell):
            row_begin = i * NPOINT_per_cell
            row_end   = (i + 1) * NPOINT_per_cell
            col_begin = i * ngrid_prim
            col_end   = (i + 1) * ngrid_prim
            basis_aux_now = input_X[row_begin:row_end, col_begin:col_end]
            
            assert np.allclose(basis_aux_now, 0) == False
                
            # check block diagonal
            
            basis_before = input_X[row_begin:row_end, :col_begin]
            assert np.allclose(basis_before, 0.0)
            basis_after  = input_X[row_begin:row_end, col_end:]
            assert np.allclose(basis_after, 0.0)
    
    _check_X_symmetry(X_unorder, Ls, mesh)
    
    nBox = [mesh[0]//Ls[0], mesh[1]//Ls[1], mesh[2]//Ls[2]]
    
    ncell = np.prod(Ls)
    ngrid_prim = np.prod(mesh) // ncell
    
    X_Diag_2 = np.zeros_like(X_Diag_bench)
        
    ### construct the factor before transform X_diag_bench_full 
    freq1 = np.array(range(nBox[0]), dtype=np.float64)
    freq2 = np.array(range(nBox[1]), dtype=np.float64)
    freq3 = np.array(range(nBox[2]), dtype=np.float64)
    freq_q = np.array(np.meshgrid(freq1, freq2, freq3, indexing='ij'))

    freq1 = np.array(range(Ls[0]), dtype=np.float64)
    freq2 = np.array(range(Ls[1]), dtype=np.float64)
    freq3 = np.array(range(Ls[2]), dtype=np.float64)
    freq_Q = np.array(np.meshgrid(freq1, freq2, freq3, indexing='ij'))
        
    FREQ = np.einsum("ijkl,ipqs->ijklpqs", freq_Q, freq_q)
    FREQ[0] /= (Ls[0] * nBox[0])
    FREQ[1] /= (Ls[1] * nBox[1])
    FREQ[2] /= (Ls[2] * nBox[2])
    FREQ = np.einsum("ijklpqs->jklpqs", FREQ)
    FREQ  = FREQ.reshape(np.prod(Ls), np.prod(nBox))
    FREQ  = np.exp(-2.0j * np.pi * FREQ)  # this is the only correct way to construct the factor
    
    for i in range(ncell):
        
        print("cell %d" % i)
        
        b_begin = i * NPOINT
        b_end   = (i + 1) * NPOINT
        k_begin = i * ngrid_prim
        k_end   = (i + 1) * ngrid_prim

        Hmat = X_unorder[b_begin:b_end, k_begin:k_end]
        
        assert np.allclose(Hmat, 0.0) == False  
        
        # before and after 
        
        Hmat_before = X_unorder[b_begin:b_end, :k_begin]
        assert np.allclose(Hmat_before, 0.0)
        Hmat_after  = X_unorder[b_begin:b_end, k_end:]
        assert np.allclose(Hmat_after, 0.0)

        bench = X_Diag_bench_full[:, k_begin:k_end]
        
        # print(bench.shape)
        # print(Hmat.shape)
        bench2 = bench * FREQ[i]
        # bench2 = bench
        # print(bench2/bench)
        bench = bench2
        bench = bench.reshape(-1, *nBox)
        # print(bench.shape)
        bench = np.fft.fftn(bench, axes=(1, 2, 3)) # shit
        bench = bench.reshape(-1, np.prod(nBox))

        assert np.allclose(Hmat, bench)
    
    ######### remove the negative frequency #########
    
    ## get the frequency of Q ## 
    
    freq1 = np.array(range(Ls[0]), dtype=np.float64)
    freq2 = np.array(range(Ls[1]), dtype=np.float64)
    freq3 = np.array(range(Ls[2]//2+1), dtype=np.float64)
    freq_Q = np.array(np.meshgrid(freq1, freq2, freq3, indexing='ij'))
    
    FREQ = np.einsum("ijkl,ipqs->ijklpqs", freq_Q, freq_q)
    FREQ[0] /= (Ls[0] * nBox[0])
    FREQ[1] /= (Ls[1] * nBox[1])
    FREQ[2] /= (Ls[2] * nBox[2])
    FREQ = np.einsum("ijklpqs->jklpqs", FREQ)
    FREQ  = FREQ.reshape(-1, np.prod(nBox))
    FREQ  = np.exp(-2.0j * np.pi * FREQ)  # this is the only correct way to construct the factor
    
    def permutation(nx, ny, nz, shift_x, shift_y, shift_z):
        
        res = np.zeros((nx*ny*nz), dtype=numpy.int32)
        
        loc_now = 0
        for ix in range(nx):
            for iy in range(ny):
                for iz in range(nz):
                    ix2 = (nx - ix - shift_x) % nx
                    iy2 = (ny - iy - shift_y) % ny
                    iz2 = (nz - iz - shift_z) % nz
                    
                    loc = ix2 * ny * nz + iy2 * nz + iz2
                    # res[loc_now] = loc
                    res[loc] = loc_now
                    loc_now += 1
        return res
    
    # pert = permutation(nBox[0], nBox[1], nBox[2])
    
    # print(pert[:10])
    
    ibox = 0
    for ix in range(Ls[0]):
        for iy in range(Ls[1]):
            for iz in range(Ls[2]//2+1):
                
                print("ix = %d iy = %d iz = %d" % (ix,iy,iz))
                print("Cell %d" % (ibox))
                
                b_begin = ibox * NPOINT
                b_end   = (ibox + 1) * NPOINT
                k_begin = ibox * ngrid_prim
                k_end   = (ibox + 1) * ngrid_prim 
                
                basis_test = X_Diag_bench[:, k_begin:k_end]
                
                basis_test = basis_test * FREQ[ibox]
                basis_test = basis_test.reshape(-1, *nBox)
                basis_test = np.fft.fftn(basis_test, axes=(1, 2, 3)) # shit
                basis_test = basis_test.reshape(-1, np.prod(nBox))
                
                
                ibox_full = ix * Ls[1] * Ls[2] + iy * Ls[2] + iz
                b_begin = ibox_full * NPOINT
                b_end   = (ibox_full + 1) * NPOINT
                k_begin = ibox_full * ngrid_prim
                k_end   = (ibox_full + 1) * ngrid_prim
                
                benchmark = X_unorder[b_begin:b_end, k_begin:k_end]
                
                # benchmark = X_Diag_bench_full[:, k_begin:k_end]
                
                assert np.allclose(basis_test, benchmark)
                
                # print(basis_test)
                # print(benchmark)
                
                ibox+=1
                
                # benchmark = benchmark.reshape(-1, *nBox)
                # benchmark = np.fft.ifftn(benchmark, axes=(1, 2, 3)) # shit
                # benchmark = benchmark.reshape(-1, np.prod(nBox))
                
                # check complex conjugate symmetry 
                
                ix2 = (Ls[0] - ix) % Ls[0]
                iy2 = (Ls[1] - iy) % Ls[1]
                iz2 = (Ls[2] - iz) % Ls[2]
                
                ibox_full2 = ix2 * Ls[1] * Ls[2] + iy2 * Ls[2] + iz2
                b_begin2 = ibox_full2 * NPOINT
                b_end2   = (ibox_full2 + 1) * NPOINT
                k_begin2 = ibox_full2 * ngrid_prim
                k_end2   = (ibox_full2 + 1) * ngrid_prim
                benchmark2 = X_unorder[b_begin2:b_end2, k_begin2:k_end2]
                
                shift_x = 0
                shift_y = 0
                shift_z = 0
                
                if ix != 0:
                    shift_x = 1
                if iy != 0:
                    shift_y = 1
                if iz != 0:
                    shift_z = 1 # only up to 8 possible! 
                
                pert = permutation(nBox[0], nBox[1], nBox[2], shift_x, shift_y, shift_z)
                
                benchmark = benchmark[:, pert].conj() # this is the k symmetry ! 
                # benchmark2 = benchmark2[:, pert].conj()
                # benchmark2 = benchmark2.conj()
                
                # print(benchmark.shape)
                # print(benchmark2.shape)
                
                # print(benchmark[1])
                # print(benchmark2[1])
                
                # benchmark2 = benchmark2.reshape(-1, *nBox)
                # benchmark2 = np.fft.ifftn(benchmark2, axes=(1, 2, 3)) # shit
                # benchmark2 = benchmark2.reshape(-1, np.prod(nBox))
                
                # print(benchmark)
                # print(benchmark2)
                
                # print(benchmark-benchmark2)
                
                assert np.allclose(benchmark, benchmark2)