#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
Newton Raphson SCF solver with augmented Hessian
'''

import sys
import time
import copy
from functools import reduce
import numpy
import scipy.linalg
import pyscf.lib
import pyscf.gto
import pyscf.symm
import pyscf.lib.logger as logger
from pyscf.scf import chkfile


APPROX_XC_HESSIAN = True

def expmat(a):
    return scipy.linalg.expm(a)

def gen_g_hop_rhf(mf, mo_coeff, mo_occ, fock_ao=None):
    mol = mf.mol
    occidx = numpy.where(mo_occ==2)[0]
    viridx = numpy.where(mo_occ==0)[0]
    nocc = len(occidx)
    nvir = len(viridx)

    if fock_ao is None:
        dm1 = mf.make_rdm1(mo_coeff, mo_occ)
        fock_ao = mf.get_hcore() + mf.get_veff(mol, dm1)
    fock = reduce(numpy.dot, (mo_coeff.T, fock_ao, mo_coeff))

    g = fock[viridx[:,None],occidx] * 2

    foo = fock[occidx[:,None],occidx]
    fvv = fock[viridx[:,None],viridx]

    h_diag = (fvv.diagonal().reshape(-1,1)-foo.diagonal()) * 2

    if hasattr(mf, 'xc'):
        if APPROX_XC_HESSIAN:
            from pyscf.dft import vxc
            x_code = vxc.parse_xc_name(mf.xc)[0]
            hyb = vxc.hybrid_coeff(x_code, spin=(mol.spin>0)+1)
        else:
            save_for_dft = [None, None]  # (dm, veff)

    def h_op(x):
        x = x.reshape(nvir,nocc)
        x2 =-numpy.einsum('sq,ps->pq', foo, x) * 2
        x2+= numpy.einsum('pr,rq->pq', fvv, x) * 2

        d1 = reduce(numpy.dot, (mo_coeff[:,viridx], x, mo_coeff[:,occidx].T))
        if hasattr(mf, 'xc'):
            if APPROX_XC_HESSIAN:
                vj, vk = mf.get_jk(mol, d1+d1.T)
                if abs(hyb) < 1e-10:
                    dvhf = vj
                else:
                    dvhf = vj - vk * hyb * .5
            else:
                if save_for_dft[0] is None:
                    save_for_dft[0] = mf.make_rdm1(mo_coeff, mo_occ)
                    save_for_dft[1] = mf.get_veff(mol, save_for_dft[0])
                dm1 = save_for_dft[0] + d1 + d1.T
                vhf1 = mf.get_veff(mol, dm1, dm_last=save_for_dft[0],
                                   vhf_last=save_for_dft[1])
                dvhf = vhf1 - save_for_dft[1]
                save_for_dft[0] = dm1
                save_for_dft[1] = vhf1
        else:
            dvhf = mf.get_veff(mol, d1+d1.T)
        x2 += reduce(numpy.dot, (mo_coeff[:,viridx].T, dvhf,
                                 mo_coeff[:,occidx])) * 4
        return x2.reshape(-1)

    return g.reshape(-1), h_op, h_diag.reshape(-1)


def gen_g_hop_rohf(mf, mo_coeff, mo_occ, fock_ao=None):
    mol = mf.mol
    occidxa = numpy.where(mo_occ>0)[0]
    occidxb = numpy.where(mo_occ==2)[0]
    viridxa = numpy.where(mo_occ==0)[0]
    viridxb = numpy.where(mo_occ<2)[0]
    mask = pyscf.scf.hf.uniq_var_indices(mo_occ)

    if fock_ao is None:
        dm1 = mf.make_rdm1(mo_coeff, mo_occ)
        fock_ao = mf.get_hcore() + mf.get_veff(mol, dm1)
    focka = reduce(numpy.dot, (mo_coeff.T, fock_ao[0], mo_coeff))
    fockb = reduce(numpy.dot, (mo_coeff.T, fock_ao[1], mo_coeff))

    g = numpy.zeros_like(focka)
    g[viridxa[:,None],occidxa]  = focka[viridxa[:,None],occidxa]
    g[viridxb[:,None],occidxb] += fockb[viridxb[:,None],occidxb]
    g = g[mask]

    h_diag = numpy.zeros_like(focka)
    h_diag[viridxa[:,None],occidxa] -= focka[occidxa,occidxa]
    h_diag[viridxa[:,None],occidxa] += focka[viridxa,viridxa].reshape(-1,1)
    h_diag[viridxb[:,None],occidxb] -= fockb[occidxb,occidxb]
    h_diag[viridxb[:,None],occidxb] += fockb[viridxb,viridxb].reshape(-1,1)
    h_diag = h_diag[mask]

    if hasattr(mf, 'xc'):
        if APPROX_XC_HESSIAN:
            from pyscf.dft import vxc
            x_code = vxc.parse_xc_name(mf.xc)[0]
            hyb = vxc.hybrid_coeff(x_code, spin=(mol.spin>0)+1)
        else:
            save_for_dft = [None, None]  # (dm, veff)
    def h_op(x):
        x1 = numpy.zeros_like(focka)
        x1[mask] = x
        x1 = x1 - x1.T
        x2 = numpy.zeros_like(focka)

        #: x2[nb:,:na] = numpy.einsum('sp,qs->pq', focka[:na,nb:], x1[:na,:na])
        #: x2[nb:,:na] += numpy.einsum('rq,rp->pq', focka[:na,:na], x1[:na,nb:])
        #: x2[na:,:na] -= numpy.einsum('sp,rp->rs', focka[:na,:na], x1[na:,:na])
        #: x2[na:,:na] -= numpy.einsum('ps,qs->pq', focka[na:], x1[:na]) * 2
        #: x2[nb:na,:nb] += numpy.einsum('qr,pr->pq', focka[:nb], x1[nb:na])
        #: x2[nb:na,:nb] -= numpy.einsum('rq,sq->rs', focka[nb:na], x1[:nb])
        #: x2[nb:,:na] += numpy.einsum('sp,qs->pq', fockb[:nb,nb:], x1[:na,:nb])
        #: x2[nb:,:na] += numpy.einsum('rq,rp->pq', fockb[:nb,:na], x1[:nb,nb:])
        #: x2[nb:,:nb] -= numpy.einsum('sp,rp->rs', fockb[:nb], x1[nb:])
        #: x2[nb:,:nb] -= numpy.einsum('rq,sq->rs', fockb[nb:], x1[:nb]) * 2
        x2[viridxb[:,None],occidxa] = \
                (numpy.einsum('sp,qs->pq', focka[occidxa[:,None],viridxb], x1[occidxa[:,None],occidxa])
                +numpy.einsum('rq,rp->pq', focka[occidxa[:,None],occidxa], x1[occidxa[:,None],viridxb]))
        x2[viridxa[:,None],occidxa] -= \
                (numpy.einsum('sp,rp->rs', focka[occidxa[:,None],occidxa], x1[viridxa[:,None],occidxa])
                +numpy.einsum('ps,qs->pq', focka[viridxa], x1[occidxa]) * 2)
        x2[occidxa[:,None],occidxb] += \
                (numpy.einsum('qr,pr->pq', focka[occidxb], x1[occidxa])
                -numpy.einsum('rq,sq->rs', focka[occidxa], x1[occidxb]))

        x2[viridxb[:,None],occidxa] += \
                (numpy.einsum('sp,qs->pq', fockb[occidxb[:,None],viridxb], x1[occidxa[:,None],occidxb])
                +numpy.einsum('rq,rp->pq', fockb[occidxb[:,None],occidxa], x1[occidxb[:,None],viridxb]))
        x2[viridxb[:,None],occidxb] -= \
                (numpy.einsum('sp,rp->rs', fockb[occidxb], x1[viridxb])
                +numpy.einsum('rq,sq->rs', fockb[viridxb], x1[occidxb]) * 2)
        x2 *= .5

        d1a = reduce(numpy.dot, (mo_coeff[:,viridxa], x1[viridxa[:,None],occidxa], mo_coeff[:,occidxa].T))
        d1b = reduce(numpy.dot, (mo_coeff[:,viridxb], x1[viridxb[:,None],occidxb], mo_coeff[:,occidxb].T))
        if hasattr(mf, 'xc'):
            if APPROX_XC_HESSIAN:
                vj, vk = mf.get_jk(mol, numpy.array((d1a+d1a.T,d1b+d1b.T)))
                if abs(hyb) < 1e-10:
                    dvhf = vj[0] + vj[1]
                else:
                    dvhf = (vj[0] + vj[1]) - vk * hyb
            else:
                from pyscf.dft import uks
                if save_for_dft[0] is None:
                    save_for_dft[0] = mf.make_rdm1(mo_coeff, mo_occ)
                    save_for_dft[1] = mf.get_veff(mol, save_for_dft[0])
                dm1 = numpy.array((save_for_dft[0][0]+d1a+d1a.T,
                                   save_for_dft[0][1]+d1b+d1b.T))
                vhf1 = uks.get_veff_(mf, mol, dm1, dm_last=save_for_dft[0],
                                     vhf_last=save_for_dft[1])
                dvhf = (vhf1[0]-save_for_dft[1][0], vhf1[1]-save_for_dft[1][1])
                save_for_dft[0] = dm1
                save_for_dft[1] = vhf1
        else:
            dvhf = mf.get_veff(mol, numpy.array((d1a+d1a.T,d1b+d1b.T)))
        x2[viridxa[:,None],occidxa] += reduce(numpy.dot, (mo_coeff[:,viridxa].T, dvhf[0], mo_coeff[:,occidxa]))
        x2[viridxb[:,None],occidxb] += reduce(numpy.dot, (mo_coeff[:,viridxb].T, dvhf[1], mo_coeff[:,occidxb]))
        return x2[mask]

    return g, h_op, h_diag


def gen_g_hop_uhf(mf, mo_coeff, mo_occ, fock_ao=None):
    mol = mf.mol
    occidxa = numpy.where(mo_occ[0]>0)[0]
    occidxb = numpy.where(mo_occ[1]>0)[0]
    viridxa = numpy.where(mo_occ[0]==0)[0]
    viridxb = numpy.where(mo_occ[1]==0)[0]
    nocca = len(occidxa)
    noccb = len(occidxb)
    nvira = len(viridxa)
    nvirb = len(viridxb)

    if fock_ao is None:
        dm1 = mf.make_rdm1(mo_coeff, mo_occ)
        fock_ao = mf.get_hcore() + mf.get_veff(mol, dm1)
    focka = reduce(numpy.dot, (mo_coeff[0].T, fock_ao[0], mo_coeff[0]))
    fockb = reduce(numpy.dot, (mo_coeff[1].T, fock_ao[1], mo_coeff[1]))

    g = numpy.hstack((focka[viridxa[:,None],occidxa].reshape(-1),
                      fockb[viridxb[:,None],occidxb].reshape(-1)))

    h_diaga =(focka[viridxa,viridxa].reshape(-1,1) - focka[occidxa,occidxa])
    h_diagb =(fockb[viridxb,viridxb].reshape(-1,1) - fockb[occidxb,occidxb])
    h_diag = numpy.hstack((h_diaga.reshape(-1), h_diagb.reshape(-1)))

    if hasattr(mf, 'xc'):
        if APPROX_XC_HESSIAN:
            from pyscf.dft import vxc
            x_code = vxc.parse_xc_name(mf.xc)[0]
            hyb = vxc.hybrid_coeff(x_code, spin=(mol.spin>0)+1)
        else:
            save_for_dft = [None, None]  # (dm, veff)
    def h_op(x):
        x1a = x[:nvira*nocca].reshape(nvira,nocca)
        x1b = x[nvira*nocca:].reshape(nvirb,noccb)
        x2a = numpy.zeros((nvira,nocca))
        x2b = numpy.zeros((nvirb,noccb))
        x2a -= numpy.einsum('sq,ps->pq', focka[occidxa[:,None],occidxa], x1a)
        x2a += numpy.einsum('rp,rq->pq', focka[viridxa[:,None],viridxa], x1a)
        x2b -= numpy.einsum('sq,ps->pq', fockb[occidxb[:,None],occidxb], x1b)
        x2b += numpy.einsum('rp,rq->pq', fockb[viridxb[:,None],viridxb], x1b)

        d1a = reduce(numpy.dot, (mo_coeff[0][:,viridxa], x1a,
                                 mo_coeff[0][:,occidxa].T))
        d1b = reduce(numpy.dot, (mo_coeff[1][:,viridxb], x1b,
                                 mo_coeff[1][:,occidxb].T))
        if hasattr(mf, 'xc'):
            if APPROX_XC_HESSIAN:
                vj, vk = mf.get_jk(mol, numpy.array((d1a+d1a.T,d1b+d1b.T)))
                if abs(hyb) < 1e-10:
                    dvhf = vj[0] + vj[1]
                else:
                    dvhf = (vj[0] + vj[1]) - vk * hyb
            else:
                if save_for_dft[0] is None:
                    save_for_dft[0] = mf.make_rdm1(mo_coeff, mo_occ)
                    save_for_dft[1] = mf.get_veff(mol, save_for_dft[0])
                dm1 = numpy.array((save_for_dft[0][0]+d1a+d1a.T,
                                   save_for_dft[0][1]+d1b+d1b.T))
                vhf1 = mf.get_veff(mol, dm1, dm_last=save_for_dft[0],
                                   vhf_last=save_for_dft[1])
                dvhf = (vhf1[0]-save_for_dft[1][0], vhf1[1]-save_for_dft[1][1])
                save_for_dft[0] = dm1
                save_for_dft[1] = vhf1
        else:
            dvhf = mf.get_veff(mol, numpy.array((d1a+d1a.T,d1b+d1b.T)))
        x2a += reduce(numpy.dot, (mo_coeff[0][:,viridxa].T, dvhf[0],
                                  mo_coeff[0][:,occidxa]))
        x2b += reduce(numpy.dot, (mo_coeff[1][:,viridxb].T, dvhf[1],
                                  mo_coeff[1][:,occidxb]))
        return numpy.hstack((x2a.ravel(), x2b.ravel()))

    return g, h_op, h_diag


# TODO: check whether high order terms in (g_orb, h_op) affects optimization
# To include high order terms, we can generate mo_coeff every time u matrix
# changed and insert the mo_coeff to g_op, h_op.
# Seems the high order terms do not help optimization?
def rotate_orb_cc(mf, mo_coeff, mo_occ, fock_ao, h1e,
                  conv_tol_grad=None, verbose=None):
    from pyscf.mcscf import mc1step
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mf.stdout, mf.verbose)

    if conv_tol_grad is None:
        conv_tol_grad = numpy.sqrt(mf.conv_tol*.1)

    t2m = (time.clock(), time.time())
    if isinstance(mo_coeff, numpy.ndarray) and mo_coeff.ndim == 2:
        nmo = mo_coeff.shape[1]
    else:
        nmo = mo_coeff[0].shape[1]
    g_orb, h_op, h_diag = mf.gen_g_hop(mo_coeff, mo_occ, fock_ao)
    norm_gkf = norm_gorb = numpy.linalg.norm(g_orb)
    log.debug('    |g|= %4.3g (keyframe)', norm_gorb)
    t3m = log.timer('gen h_op', *t2m)

    def precond(x, e):
        hdiagd = h_diag-(e-mf.ah_level_shift)
        hdiagd[abs(hdiagd)<1e-8] = 1e-8
        x = x/hdiagd
## Because of DFT, donot norm to 1 which leads 1st DM too large.
#        norm_x = numpy.linalg.norm(x)
#        if norm_x < 1e-2:
#            x *= 1e-2/norm_x
        return x

    kf_compensate = 0
    kf_trust_region = 0.25
    g_op = lambda: g_orb
    x0_guess = g_orb
    while True:
        if norm_gorb < .1:
            max_cycle = mf.max_cycle_inner-int(numpy.log(norm_gorb+1e-9))
        else:
            max_cycle = mf.max_cycle_inner
        ah_conv_tol = min(norm_gorb**2, mf.ah_conv_tol)
        # increase the AH accuracy when approach convergence
        ah_start_tol = (numpy.log(norm_gorb+conv_tol_grad) -
                        numpy.log(min(norm_gorb,conv_tol_grad))) * 1.5 * norm_gorb
        ah_start_tol = max(min(ah_start_tol, mf.ah_start_tol), ah_conv_tol)
        #ah_start_cycle = max(mf.ah_start_cycle, int(-numpy.log10(norm_gorb)))
        ah_start_cycle = mf.ah_start_cycle
        log.debug('Set ah_start_tol %g, ah_start_cycle %d, max_cycle %d',
                  ah_start_tol, ah_start_cycle, max_cycle)
        g_orb0 = g_orb
        imic = 0
        dr = 0
        jkcount = 0
        ikf = 0
        dm0 = mf.make_rdm1(mo_coeff, mo_occ)
        vhf0 = fock_ao - h1e

        for ah_end, ihop, w, dxi, hdxi, residual, seig \
                in mc1step.davidson_cc(h_op, g_op, precond, x0_guess,
                                       tol=ah_conv_tol, max_cycle=mf.ah_max_cycle,
                                       lindep=mf.ah_lindep, verbose=log):
            norm_residual = numpy.linalg.norm(residual)
            if (ah_end or ihop == mf.ah_max_cycle or # make sure to use the last step
                ((norm_residual < ah_start_tol) and (ihop >= ah_start_cycle)) or
                (seig < mf.ah_lindep)):
                imic += 1
                dxmax = numpy.max(abs(dxi))
                if dxmax > mf.max_stepsize:
                    scale = mf.max_stepsize / dxmax
                    log.debug1('... scale rotation size %g', scale)
                    dxi *= scale
                    hdxi *= scale
                else:
                    scale = None

                dr = dr + dxi
                g_orb = g_orb + hdxi
                norm_dr = numpy.linalg.norm(dr)
                norm_gorb = numpy.linalg.norm(g_orb)
                norm_dxi = numpy.linalg.norm(dxi)
                log.debug('    imic %d(%d)  |g|= %4.3g  |dxi|= %4.3g  '
                          'max(|x|)= %4.3g  |dr|= %4.3g  eig= %4.3g  seig= %4.3g',
                          imic, ihop, norm_gorb, norm_dxi,
                          dxmax, norm_dr, w, seig)

                ikf += 1
                if imic > 3 and norm_gorb > norm_gkf*mf.ah_grad_trust_region:
                    g_orb = g_orb - hdxi
                    dr = dr - dxi
                    norm_gorb = numpy.linalg.norm(g_orb)
                    log.debug('|g| >> keyframe, Restore previouse step')
                    break

                elif (imic >= max_cycle or norm_gorb < conv_tol_grad*.8):
                    break

                elif (ikf > 2 and # avoid frequent keyframe
                      (ikf > (mf.keyframe_interval - kf_compensate
                              -numpy.log(norm_dr)*mf.keyframe_interval_rate) or
                       norm_gorb < norm_gkf*kf_trust_region)):
                    ikf = 0
                    u = mf.update_rotate_matrix(dr, mo_occ)
                    mo1 = mf.update_mo_coeff(mo_coeff, u)
                    dm = mf.make_rdm1(mo1, mo_occ)
# use mf._scf.get_veff to avoid density-fit mf polluting get_veff
                    vhf0 = mf._scf.get_veff(mf.mol, dm, dm_last=dm0, vhf_last=vhf0)
                    dm0 = dm
                    g_kf = mf.get_grad(mo1, mo_occ, h1e+vhf0)
                    norm_gkf = numpy.linalg.norm(g_kf)
                    norm_dg = numpy.linalg.norm(g_kf-g_orb)
                    jkcount += 1
                    log.debug('Adjust keyframe g_orb to |g|= %4.3g  '
                              '|g-correction|= %4.3g', norm_gkf, norm_dg)
# kf_compensate:  If the keyframe and the esitimated g_orb are too different,
# insert more keyframes for the following optimization
                    kf_compensate = norm_dg / norm_gorb
                    if kf_compensate > mf.ah_grad_trust_region:
                        g_orb = g_orb - hdxi
                        dr = dr - dxi
                        norm_gorb = numpy.linalg.norm(g_orb)
                        log.debug('Out of trust region. Restore previouse step')
                        break
                    else:
                        kf_trust_region = max(min(kf_compensate, 0.25), .05)
                        log.debug1('... kf_compensate = %g  kf_trust_region = %g',
                                   kf_compensate, kf_trust_region)
                        g_orb, g_kf = g_kf, None
                        norm_gorb = norm_gkf

                if scale is None:
                    ah_start_tol = max(norm_gorb * 1.2,
                                       ah_start_tol * mf.ah_decay_rate)
                    log.debug('Set ah_start_tol %g', ah_start_tol)

        u = mf.update_rotate_matrix(dr, mo_occ)
        jkcount += ihop + 1
        log.debug('    tot inner=%d  %d JK  |g|= %4.3g  |u-1|= %4.3g',
                  imic, jkcount, norm_gorb,
                  numpy.linalg.norm(u-numpy.eye(nmo)))
        h_op = h_diag = None
        t3m = log.timer('aug_hess in %d inner iters' % imic, *t3m)
        mo_coeff, mo_occ, fock_ao = (yield u, g_orb0, jkcount)

        g_kf, h_op, h_diag = mf.gen_g_hop(mo_coeff, mo_occ, fock_ao)
        norm_gkf = numpy.linalg.norm(g_kf)
        norm_dg = numpy.linalg.norm(g_kf-g_orb)
        log.debug('    |g|= %4.3g (keyframe), |g-correction|= %4.3g',
                  norm_gkf, norm_dg)
        kf_compensate = norm_dg / norm_gorb
        kf_trust_region = max(min(kf_compensate, 0.25), .05)
        log.debug1('... kf_compensate = %g  kf_trust_region = %g',
                   kf_compensate, kf_trust_region)
        g_orb, g_kf = g_kf, None
        norm_gorb = norm_gkf
        x0_guess = dxi


def kernel(mf, mo_coeff, mo_occ, conv_tol=1e-10, conv_tol_grad=None,
           max_cycle=50, dump_chk=True,
           callback=None, verbose=logger.NOTE):
    cput0 = (time.clock(), time.time())
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mf.stdout, verbose)
    mol = mf.mol
    if conv_tol_grad is None:
        conv_tol_grad = numpy.sqrt(conv_tol)
        logger.info(mf, 'Set conv_tol_grad to %g', conv_tol_grad)
    scf_conv = False
    hf_energy = mf.hf_energy

    h1e = mf.get_hcore(mol)
    s1e = mf.get_ovlp(mol)
    dm = mf.make_rdm1(mo_coeff, mo_occ)
# call mf._scf.get_veff, to avoid density_fit module polluting get_veff function
    vhf = mf._scf.get_veff(mol, dm)
    fock = mf.get_fock(h1e, s1e, vhf, dm, 0, None)
    mo_energy = mf.get_mo_energy(fock, s1e, dm)[0]
    mf.get_occ(mo_energy, mo_coeff)

    if dump_chk:
        chkfile.save_mol(mol, mf.chkfile)

    rotaiter = rotate_orb_cc(mf, mo_coeff, mo_occ, fock, h1e, conv_tol_grad, log)
    u, g_orb, jkcount = rotaiter.next()
    jktot = jkcount
    cput1 = log.timer('initializing second order scf', *cput0)

    for imacro in range(max_cycle):
        dm_last = dm
        last_hf_e = hf_energy
        norm_gorb = numpy.linalg.norm(g_orb)
        mo_coeff = mf.update_mo_coeff(mo_coeff, u)
        dm = mf.make_rdm1(mo_coeff, mo_occ)
        vhf = mf._scf.get_veff(mol, dm, dm_last=dm_last, vhf_last=vhf)
        fock = mf.get_fock(h1e, s1e, vhf, dm, imacro, None)
        mo_energy = mf.get_mo_energy(fock, s1e, dm)[0]
        mf.get_occ(mo_energy, mo_coeff)
# call mf._scf.energy_tot for dft, because the (dft).get_veff step saved _exc in mf._scf
        hf_energy = mf._scf.energy_tot(dm, h1e, vhf)

        log.info('macro= %d  E= %.15g  delta_E= %g  |g|= %g  %d JK',
                 imacro, hf_energy, hf_energy-last_hf_e, norm_gorb,
                 jkcount)
        cput1 = log.timer('cycle= %d'%(imacro+1), *cput1)

        if (abs((hf_energy-last_hf_e)/hf_energy)*1e2 < conv_tol and
            norm_gorb < conv_tol_grad):
            scf_conv = True

        if dump_chk:
            mf.dump_chk(locals())

        if callable(callback):
            callback(locals())

        if scf_conv:
            break

        u, g_orb, jkcount = rotaiter.send((mo_coeff, mo_occ, fock))
        jktot += jkcount

    rotaiter.close()
    mo_energy, mo_coeff = mf.get_mo_energy(fock, s1e, dm)
    mo_occ = mf.get_occ(mo_energy, mo_coeff)
    log.info('macro X = %d  E=%.15g  |g|= %g  total %d JK',
             imacro+1, hf_energy, norm_gorb, jktot)
    return scf_conv, hf_energy, mo_energy, mo_coeff, mo_occ


def newton(mf):
    import pyscf.scf
    from pyscf.mcscf import mc1step_symm
    class Base(mf.__class__):
        def __init__(self):
            self._scf = mf
            self.max_cycle_inner = 10
            self.max_stepsize = .05

            self.ah_start_tol = 5.
            self.ah_start_cycle = 1
            self.ah_level_shift = 0
            self.ah_conv_tol = 1e-12
            self.ah_lindep = 1e-14
            self.ah_max_cycle = 30
            self.ah_grad_trust_region = 3.
# * Classic AH can be simulated by setting
#               max_cycle_micro_inner = 1
#               ah_start_tol = 1e-7
#               max_stepsize = 1.5
#               ah_grad_trust_region = 1e6
            self.ah_decay_rate = .8
            self.keyframe_interval = 5
            self.keyframe_interval_rate = 1.
            self_keys = set(self.__dict__.keys())

            self.__dict__.update(mf.__dict__)
            self._keys = self_keys.union(mf._keys)

        def dump_flags(self):
            log = logger.Logger(self.stdout, self.verbose)
            log.info('\n')
            log.info('******** SCF Newton Raphson flags ********')
            log.info('SCF tol = %g', self.conv_tol)
            log.info('conv_tol_grad = %s',    self.conv_tol_grad)
            log.info('max. SCF cycles = %d', self.max_cycle)
            log.info('direct_scf = %s', self._scf.direct_scf)
            if self._scf.direct_scf:
                log.info('direct_scf_tol = %g', self._scf.direct_scf_tol)
            if self.chkfile:
                log.info('chkfile to save SCF result = %s', self.chkfile)
            log.info('max_cycle_inner = %d',  self.max_cycle_inner)
            log.info('max_stepsize = %g', self.max_stepsize)
            log.info('ah_start_tol = %g',     self.ah_start_tol)
            log.info('ah_level_shift = %g',   self.ah_level_shift)
            log.info('ah_conv_tol = %g',      self.ah_conv_tol)
            log.info('ah_lindep = %g',        self.ah_lindep)
            log.info('ah_start_cycle = %d',   self.ah_start_cycle)
            log.info('ah_max_cycle = %d',     self.ah_max_cycle)
            log.info('ah_grad_trust_region = %g', self.ah_grad_trust_region)
            log.info('keyframe_interval = %d', self.keyframe_interval)
            log.info('keyframe_interval_rate = %g', self.keyframe_interval_rate)
            log.info('augmented hessian decay rate = %g', self.ah_decay_rate)
            log.info('max_memory %d MB (current use %d MB)',
                     self.max_memory, pyscf.lib.current_memory()[0])

        def get_fock_(self, h1e, s1e, vhf, dm, cycle=-1, adiis=None,
                      diis_start_cycle=None, level_shift_factor=None,
                      damp_factor=None):
            return h1e + vhf

        def kernel(self, mo_coeff=None, mo_occ=None):
            if mo_coeff is None: mo_coeff = self.mo_coeff
            if mo_occ is None: mo_occ = self.mo_occ
            cput0 = (time.clock(), time.time())

            self.build(self.mol)
            self.dump_flags()
            self.converged, self.hf_energy, \
                    self.mo_energy, self.mo_coeff, self.mo_occ = \
                    kernel(self, mo_coeff, mo_occ, conv_tol=self.conv_tol,
                           conv_tol_grad=self.conv_tol_grad,
                           max_cycle=self.max_cycle,
                           callback=self.callback, verbose=self.verbose)

            logger.timer(self, 'Second order SCF', *cput0)
            self._finalize_()
            return self.hf_energy

        def from_dm(self, dm):
            '''Transform density matrix to the initial guess'''
            mol = self.mol
            h1e = self.get_hcore(mol)
            s1e = self.get_ovlp(mol)
            vhf = self._scf.get_veff(mol, dm)
            fock = self.get_fock(h1e, s1e, vhf, dm, 0, None)
            mo_energy, mo_coeff = self.get_mo_energy(fock, s1e, dm)
            mo_occ = self.get_occ(mo_energy, mo_coeff)
            return mo_coeff, mo_occ

    if isinstance(mf, pyscf.scf.rohf.ROHF):
        class ROHF(Base):
            def gen_g_hop(self, mo_coeff, mo_occ, fock_ao=None, h1e=None):
                if self.mol.symmetry:
                    self._orbsym = pyscf.symm.label_orb_symm(self.mol,
                            self.mol.irrep_id, self.mol.symm_orb, mo_coeff,
                            s=self.get_ovlp())
                return gen_g_hop_rohf(self, mo_coeff, mo_occ, fock_ao)

            def update_rotate_matrix(self, dx, mo_occ, u0=1):
                dr = pyscf.scf.hf.unpack_uniq_var(dx, mo_occ)
                if hasattr(self, '_orbsym'):
                    dr = mc1step_symm._symmetrize(dr, self._orbsym, None)
                return numpy.dot(u0, expmat(dr))

            def update_mo_coeff(self, mo_coeff, u):
                return numpy.dot(mo_coeff, u)

            def get_fock_(self, h1e, s1e, vhf, dm, cycle=-1, adiis=None,
                          diis_start_cycle=None, level_shift_factor=None,
                          damp_factor=None):
                fock = h1e + vhf
                self._focka_ao = fock[0]
                return fock

            def get_mo_energy(self, fock, s1e, dm):
                focka_ao, fockb_ao = fock
                self._focka_ao = focka_ao
                fc = (focka_ao + fockb_ao) * .5
# Projector for core, open-shell, and virtual
                nao = s1e.shape[0]
                pc = numpy.dot(dm[1], s1e)
                po = numpy.dot(dm[0]-dm[1], s1e)
                pv = numpy.eye(nao) - numpy.dot(dm[0], s1e)
                f  = reduce(numpy.dot, (pc.T, fc, pc)) * .5
                f += reduce(numpy.dot, (po.T, fc, po)) * .5
                f += reduce(numpy.dot, (pv.T, fc, pv)) * .5
                f += reduce(numpy.dot, (po.T, fockb_ao, pc))
                f += reduce(numpy.dot, (po.T, focka_ao, pv))
                f += reduce(numpy.dot, (pv.T, fc, pc))
                f = f + f.T
                return self.eig(f, s1e)
                #fc = numpy.dot(fock[0], mo_coeff)
                #mo_energy = numpy.einsum('pk,pk->k', mo_coeff, fc)
                #return mo_energy
        return ROHF()

    elif isinstance(mf, pyscf.scf.uhf.UHF):
        class UHF(Base):
            def gen_g_hop(self, mo_coeff, mo_occ, fock_ao=None, h1e=None):
                if self.mol.symmetry:
                    ovlp_ao = self.get_ovlp()
                    self._orbsym =(pyscf.symm.label_orb_symm(self.mol,
                                            self.mol.irrep_id, self.mol.symm_orb,
                                            mo_coeff[0], s=ovlp_ao),
                                   pyscf.symm.label_orb_symm(self.mol,
                                            self.mol.irrep_id, self.mol.symm_orb,
                                            mo_coeff[1], s=ovlp_ao))
                return gen_g_hop_uhf(self, mo_coeff, mo_occ, fock_ao)

            def update_rotate_matrix(self, dx, mo_occ, u0=1):
                nmo = len(mo_occ[0])
                occidxa = mo_occ[0]==1
                viridxa = numpy.logical_not(occidxa)
                occidxb = mo_occ[1]==1
                viridxb = numpy.logical_not(occidxb)
                dr = numpy.zeros((2,nmo,nmo))
                idx = numpy.array((viridxa[:,None]&occidxa,
                                   viridxb[:,None]&occidxb))
                dr[idx] = dx
                dr[0] = dr[0] - dr[0].T
                dr[1] = dr[1] - dr[1].T
                if hasattr(self, '_orbsym'):
                    dr = (mc1step_symm._symmetrize(dr[0], self._orbsym[0], None),
                          mc1step_symm._symmetrize(dr[1], self._orbsym[1], None))
                if isinstance(u0, int) and u0 == 1:
                    return numpy.array((expmat(dr[0]), expmat(dr[1])))
                else:
                    return numpy.array((numpy.dot(u0[0], expmat(dr[0])),
                                        numpy.dot(u0[1], expmat(dr[1]))))

            def update_mo_coeff(self, mo_coeff, u):
                return numpy.array(map(numpy.dot, mo_coeff, u))

            def get_mo_energy(self, fock, s1e, dm):
                return self.eig(fock, s1e)
                #fca = numpy.dot(fock[0], mo_coeff[0])
                #fcb = numpy.dot(fock[1], mo_coeff[1])
                #mo_energy =(numpy.einsum('pk,pk->k', mo_coeff[0], fca),
                #            numpy.einsum('pk,pk->k', mo_coeff[1], fcb))
                #return numpy.array(mo_energy)
        return UHF()

    elif isinstance(mf, pyscf.scf.dhf.UHF):
        raise RuntimeError('Not support Dirac-HF')

    else:
        class RHF(Base):
            def gen_g_hop(self, mo_coeff, mo_occ, fock_ao=None, h1e=None):
                if self.mol.symmetry:
                    self._orbsym = pyscf.symm.label_orb_symm(self.mol,
                            self.mol.irrep_id, self.mol.symm_orb, mo_coeff,
                            s=self.get_ovlp())
                return gen_g_hop_rhf(self, mo_coeff, mo_occ, fock_ao)

            def update_rotate_matrix(self, dx, mo_occ, u0=1):
                nmo = len(mo_occ)
                occidx = mo_occ==2
                viridx = numpy.logical_not(occidx)
                dr = numpy.zeros((nmo,nmo))
                dr[viridx[:,None]&occidx] = dx
                dr = dr - dr.T
                if hasattr(self, '_orbsym'):
                    dr = mc1step_symm._symmetrize(dr, self._orbsym, None)
                return numpy.dot(u0, expmat(dr))

            def update_mo_coeff(self, mo_coeff, u):
                return numpy.dot(mo_coeff, u)

            def get_mo_energy(self, fock, s1e, dm):
                return self.eig(fock, s1e)
                #fc = numpy.dot(fock, mo_coeff)
                #mo_energy = numpy.einsum('pk,pk->k', mo_coeff, fc)
                #return mo_energy
        return RHF()


if __name__ == '__main__':
    from pyscf import gto
    from pyscf import scf
    import pyscf.fci
    from pyscf.mcscf import addons

    mol = gto.Mole()
    mol.verbose = 0
    mol.atom = [
        ['H', ( 1.,-1.    , 0.   )],
        ['H', ( 0.,-1.    ,-1.   )],
        ['H', ( 1.,-0.5   ,-1.   )],
        ['H', ( 0., 1.    , 1.   )],
        ['H', ( 0., 0.5   , 1.   )],
        ['H', ( 1., 0.    ,-1.   )],
    ]

    mol.basis = '6-31g'
    mol.build()

    nmo = mol.nao_nr()
    m = scf.RHF(mol)
    m.scf()
    kernel(newton(m), m.mo_coeff, m.mo_occ, verbose=5)

    m.max_cycle = 1
    m.scf()
    e0 = kernel(newton(m), m.mo_coeff, m.mo_occ, verbose=5)[1]

#####################################
    mol.basis = '6-31g'
    mol.spin = 2
    mol.build(0, 0)
    m = scf.RHF(mol)
    m.max_cycle = 1
    #m.verbose = 5
    m.scf()
    e1 = kernel(newton(m), m.mo_coeff, m.mo_occ, max_cycle=50, verbose=5)[1]

    m = scf.UHF(mol)
    m.max_cycle = 1
    #m.verbose = 5
    m.scf()
    e2 = kernel(newton(m), m.mo_coeff, m.mo_occ, max_cycle=50, verbose=5)[1]

    m = scf.UHF(mol)
    m.max_cycle = 1
    #m.verbose = 5
    m.scf()
    nrmf = scf.density_fit(newton(m))
    nrmf.max_cycle = 50
    nrmf.conv_tol = 1e-8
    nrmf.conv_tol_grad = 1e-5
    #nrmf.verbose = 5
    e4 = nrmf.kernel()

    m = scf.density_fit(scf.UHF(mol))
    m.max_cycle = 1
    #m.verbose = 5
    m.scf()
    nrmf = scf.density_fit(newton(m))
    nrmf.max_cycle = 50
    nrmf.conv_tol_grad = 1e-5
    e5 = nrmf.kernel()

    print(e0 - -2.93707955256)
    print(e1 - -2.99456398848)
    print(e2 - -2.99663808314)
    print(e4 - -2.99663808186)
    print(e5 - -2.99634506072)
