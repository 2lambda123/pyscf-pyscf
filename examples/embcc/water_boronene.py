import sys
import logging
import argparse
import functools

import numpy as np
from mpi4py import MPI

import pyscf
import pyscf.gto
import pyscf.scf
from pyscf import molstructures
from pyscf import embcc

from util import run_benchmarks

MPI_comm = MPI.COMM_WORLD
MPI_rank = MPI_comm.Get_rank()
MPI_size = MPI_comm.Get_size()

log = logging.getLogger(__name__)

parser = argparse.ArgumentParser(allow_abbrev=False)
parser.add_argument("--basis", nargs=2, default=["cc-pVDZ", "cc-pVDZ"])
#parser.add_argument("--basis", default="aug-cc-pVDZ")
#parser.add_argument("--basis", nargs=2, default=["aug-cc-pVDZ", "cc-pVDZ"])
parser.add_argument("--solver", choices=["CISD", "CCSD", "FCI"], default="CCSD")
parser.add_argument("--benchmarks", nargs="*")
#parser.add_argument("--tol-bath", type=float, default=1e-3)
parser.add_argument("--distances", type=float, nargs="*",
        default=[2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0])
parser.add_argument("--distances-range", type=float, nargs=3, default=[2.8, 8.0, 0.2])
parser.add_argument("--local-type", choices=["IAO", "AO", "LAO"], default="IAO")
parser.add_argument("--bath-type", default="mp2-natorb")
parser.add_argument("--bath-size", type=float, nargs=2)
parser.add_argument("--bath-tol", type=float, nargs=2)
parser.add_argument("--dmet-bath-tol", type=float)
#parser.add_argument("--impurity", nargs="*", default=["O*", "H*", "N1", "B2"])
parser.add_argument("--impurity", nargs="*")
parser.add_argument("--impurity-number", type=int, default=1)
#parser.add_argument("--mp2-correction", action="store_true")
#parser.add_argument("--use-ref-orbitals-bath", type=int, default=0)
parser.add_argument("--minao", default="minao")

#parser.add_argument("--counterpoise", choices=["none", "water", "water-full", "borazine", "borazine-full"])
parser.add_argument("--fragment", choices=["all", "water", "boronene"], default="all")
parser.add_argument("-o", "--output", default="energies.txt")
args, restargs = parser.parse_known_args()
sys.argv[1:] = restargs

#args.use_ref_orbitals_bath = bool(args.use_ref_orbitals_bath)

if args.distances is None:
    args.distances = np.arange(args.distances_range[0], args.distances_range[1]+1e-14, args.distances_range[2])
del args.distances_range

if args.impurity is None:
    #args.impurity = ["O*", "H*"]
    args.impurity = ["H1", "O2", "H3"]
    if args.impurity_number >= 1:
        args.impurity += ["N1"]
    if args.impurity_number >= 2:
        args.impurity += ["B2"]
    if args.impurity_number >= 3:
        args.impurity += ["N3"]
    if args.impurity_number >= 4:
        raise NotImplementedError()

if MPI_rank == 0:
    log.info("Parameters")
    log.info("----------")
    for name, value in sorted(vars(args).items()):
        log.info("%10s: %r", name, value)

structure_builder = molstructures.build_water_boronene

dm0 = None
refdata = None

basis_dict = {
     #"O1" : args.basis[0],
     "H1" : args.basis[0],
     #"H2" : args.basis[0],
     "N1" : args.basis[0],
     "default" : args.basis[1]
     }

for idist, dist in enumerate(args.distances):

    if MPI_rank == 0:
        log.info("Distance=%.2f", dist)
        log.info("=============")

    #mol = structure_builder(dist, counterpoise=args.counterpoise, basis=args.basis, verbose=4)
    #mol = structure_builder(dist, basis=args.basis, verbose=4)
    mol = structure_builder(dist, basis=basis_dict, verbose=4)

    if args.fragment != "all":
        #water, boronene = mol.make_counterpoise_fragments([["O*", "H*"]])
        water, boronene = mol.make_counterpoise_fragments([["H1", "O2", "H3"]])
        if args.fragment == "water":
            mol = water
        else:
            mol = boronene

    mf = pyscf.scf.RHF(mol)
    #mf = mf.density_fit()
    t0 = MPI.Wtime()
    mf.kernel(dm0=dm0)
    log.info("Time for mean-field: %.2g", MPI.Wtime()-t0)
    assert mf.converged
    dm0 = mf.make_rdm1()

    if args.benchmarks:
        run_benchmarks(mf, args.benchmarks, dist, "benchmarks.txt", print_header=(idist==0))
        #energies = []
        #for bm in args.benchmarks:
        #    t0 = MPI.Wtime()
        #    if bm == "MP2":
        #        import pyscf.mp
        #        mp2 = pyscf.mp.MP2(mf)
        #        mp2.kernel()
        #        energies.append(mf.e_tot + mp2.e_corr)
        #    elif bm == "CISD":
        #        import pyscf.ci
        #        ci = pyscf.ci.CISD(mf)
        #        ci.kernel()
        #        assert ci.converged
        #        energies.append(mf.e_tot + ci.e_corr)
        #    elif bm == "CCSD":
        #        import pyscf.cc
        #        cc = pyscf.cc.CCSD(mf)
        #        cc.kernel()
        #        assert cc.converged
        #        energies.append(mf.e_tot + cc.e_corr)
        #    elif bm == "FCI":
        #        import pyscf.fci
        #        fci = pyscf.fci.FCI(mol, mf.mo_coeff)
        #        fci.kernel()
        #        assert fci.converged
        #        energies.append(mf.e_tot + fci.e_corr)
        #    log.info("Time for %s: %.2g", bm, MPI.Wtime()-t0)

        #if idist == 0:
        #    with open(args.output, "w") as f:
        #        f.write("#distance  HF  " + "  ".join(args.benchmarks) + "\n")
        #with open(args.output, "a") as f:
        #    f.write(("%.3f  %.8e" + (len(args.benchmarks)*"  %.8e") + "\n") % (dist, mf.e_tot, *energies))

    else:
        cc = embcc.EmbCC(mf,
                local_type=args.local_type,
                minao=args.minao,
                dmet_bath_tol=args.dmet_bath_tol,
                bath_type=args.bath_type, bath_size=args.bath_size, bath_tol=args.bath_tol,
                #mp2_correction=args.mp2_correction,
                #use_ref_orbitals_bath=args.use_ref_orbitals_bath,
                )
        cc.make_atom_cluster(args.impurity)
        if idist == 0 and MPI_rank == 0:
            cc.print_clusters()

        #cc.set_refdata(refdata)
        cc.run()
        #refdata = cc.get_refdata()

        if MPI_rank == 0:
            if idist == 0:
                with open(args.output, "a") as f:
                    #f.write("#IRC  HF  EmbCC  EmbCC(vir)  EmbCC(dem)  EmbCC(dMP2)  EmbCC(vir,dMP2)  Embcc(dem,dMP2)\n")
                    f.write("#IRC  HF  EmbCC  dMP2  EmbCC+dMP2  EmbCC(full)\n")
            with open(args.output, "a") as f:
                f.write(("%3f" + 5*"  %12.8e" + "\n") % (dist, mf.e_tot, cc.e_tot, cc.e_delta_mp2, cc.e_tot+cc.e_delta_mp2, mf.e_tot+cc.e_corr_full))
