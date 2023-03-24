'''

author: Linus Bjarne Dittmer

'''

import numpy
import numpy.linalg
import scipy
import scipy.linalg
import scipy.special
import pyscf.scf
import pyscf.dft
import pyscf.symm
import pyscf.soscf.newton_ah as newton_ah
import pyscf.soscf.sigma_utils as sigutils
import itertools

from pyscf.lib import logger


class M3SOSCF:
    '''
    Attributes for M3SOSCF:
        mf: SCF Object
            SCF Object that is to be converged. Currently only RHF permissible.
        threads: int > 0
            Number of theoretically parallel threads that is to be used. Generally increases speed
        purgeSolvers: float, optional
            Partition of solvers that is to be purged and each iteration. Between 0 and 1
        convergence: int
            Convergence Threshold for Trust is 10**-convergence
        initScattering: float
            Initial statistical distribution of subconverger guesses. The original initial guess (e.g. minao, huckel,
            1e, ...) is always conserved as the 0th guess, the rest is scattered around with uniform radius 
            distribution and uniform angular distribution on a box

        Examples:

        >>> mol = gto.M('C 0.0 0.0 0.0; O 0.0 0.0 1.1')
        >>> mf = scf.RHF(mol)
        >>> threads = 5
        >>> m3 = M3SOSCF(threads)
        >>> m3.converge()
    '''

    # scf object, currently only RHF, RKS, UHF and UKS implemented
    mf = None
    # density matrix of the current state. Used for import/export and to get the size of the system
    current_dm = None
    # Subconverger Redistribution Handler
    subconverger_rm = None
    # String identifier of used method. currently supported: RHF
    method = ''

    # Trust array (threads,)
    current_trusts = None
    # Array of Indices where the individual Subconvergers put their solution and trust
    # subconverger_indices[(Converger)] = (Index of Converger in current_trusts/mo_coeffs)
    subconverger_indices = None
    # MO Coeffs of each subconverger (threads,n,n). Used for storage of local solutions
    mo_coeffs = None
    # MO Coeff index that scrolls through the mo_coeffs array for memory purposes. Irrelevant if meM3 is turned off.
    mo_cursor = 0
    # MO Coeffs used for initialisation (n,n)
    mo_basis_coeff = None
    # Energy array (threads,)
    current_energies = None
    # Number of Subconvergers
    threads = 0
    # Array ob Subconverger Objects
    subconvergers = None
    # Gradient threshold for convergence
    convergence_thresh = 10**-5
    # Percentage / 100 of Subconvergers that are deleted and redone according to solution density each iteration
    purge_subconvergers = 0.0
    # Initial scattering of Subconvergers
    init_scattering = 0.0
    # Stepsize in NR step
    nr_stepsize = 0.0

    max_cycle = 200



    def __init__(self, mf, threads, purgeSolvers=0.5, convergence=8, initScattering=0.3, 
            trustScaleRange=(0.05, 0.5, 0.5), memSize=1, memScale=0.2, initGuess='minao', stepsize=0.2):

        self.mf = mf

        if isinstance(self.mf, pyscf.dft.uks.UKS):
            self.method = 'uks'
        elif isinstance(self.mf, pyscf.dft.rks.KohnShamDFT):
            self.method = 'rks'
        elif isinstance(self.mf, pyscf.scf.uhf.UHF):
            self.method = 'uhf'
        elif isinstance(self.mf, pyscf.scf.rohf.ROHF):
            self.method = 'rohf'
        elif isinstance(self.mf, pyscf.scf.hf.RHF):
            self.method = 'rhf'
        else:
            raise Exception('Only HF permitted in current version.')
       
        if self.method == 'uhf' or self.method == 'uks':
            self.current_dm = numpy.zeros((2, self.mf.mol.nao_nr(), self.mf.mol.nao_nr()))
            self.mo_coeffs = numpy.zeros((memSize, threads, 2, self.mf.mol.nao_nr(), self.mf.mol.nao_nr()))
        else:
            self.current_dm = numpy.zeros((self.mf.mol.nao_nr(), self.mf.mol.nao_nr()))
            self.mo_coeffs = numpy.zeros((memSize, threads, self.mf.mol.nao_nr(), self.mf.mol.nao_nr()))
        self.threads = threads
        self.current_trusts = numpy.zeros((memSize, threads))
        self.current_energies = numpy.zeros(threads)
        self.mo_basis_coeff = numpy.zeros(self.current_dm.shape)
        self.subconvergers = []
        self.subconverger_rm = SubconvergerReassigmentManager(self)
        self.init_scattering = initScattering
        self.subconverger_rm.trust_scale_range = trustScaleRange
        self.subconverger_rm.mem_scale = memScale
        self.nr_stepsize = stepsize
        self.mo_cursor = 0
        self.max_cycle = self.mf.max_cycle

        for i in range(threads):
            self.subconvergers.append(Subconverger(self))


        self.subconverger_indices = numpy.arange(len(self.subconvergers))
        self.purge_subconvergers = purgeSolvers
        self.convergence_thresh = 10**-convergence


        if not type(initGuess) is numpy.ndarray:
            self.initDensityMatrixWithRothaanStep(self.mf.get_init_guess(key=initGuess))
        else:
            self.initDensityMatrixDirectly(initGuess)



    def getDegreesOfFreedom(self):
        '''
        Returns the number of Degrees Of Freedom: N(N-1)/2
        '''
        return int(0.5 * len(self.current_dm[0]) * (len(self.current_dm[0])-1))

    def initDensityMatrixDirectly(self, idc):
        self.mo_basis_coeff = idc
        mo_pe = None
        if self.method == 'uks' or self.method == 'uhf':
            mo_pe = numpy.array((numpy.arange(len(idc[0])), numpy.arange(len(idc[0]))))
        else:
            mo_pe = numpy.array(numpy.arange(len(idc[0])))
        mo_occ = self.mf.get_occ(mo_pe, idc)
        self.mf.mo_occ = mo_occ
        self.setCurrentDm(self.mf.make_rdm1(idc, mo_occ))

    def initDensityMatrixWithRothaanStep(self, idm=None):
        '''
        Initialises the M3SOSCF-Solver with a given density matrix. One Rothaan step is performed afterwards to
        ensure DM properties.

        Arguments:
            idm: 2D array
                Density Matrix used for initialisation
        '''

        mf = self.mf
        it_num = 1
        mo_coeff = None
        if self.method == 'rohf':
            it_num = 2
        for i in range(it_num):
            fock = mf.get_fock(dm=idm)
            mo_energy, mo_coeff = mf.eig(fock, mf.get_ovlp())
            mo_occ = mf.get_occ(mo_energy, mo_coeff)
            self.mf.mo_occ = mo_occ
            idm = mf.make_rdm1(mo_coeff, mo_occ)
        self.mo_basis_coeff = mo_coeff
        self.setCurrentDm(idm)


    def setCurrentDm(self, dm, convertToMO=False):
        '''
        Overrides the current density matrix.

        Arguments:
            dm: 2D array
                New density matrix
            convertToMO: Boolean, optional, default False
                If True, converts the density matrix to MO space via D_{MO} = 0.5 SD_{AO}
        '''
        if convertToMO:
            self.current_dm = 0.5 * self.mf.get_ovlp() @ dm
        else:
            self.current_dm = dm

    def getCurrentDm(self, convertToAO=False):
        '''
        Returns the current density matrix. Possibility of desync, since density matrix is not regularly updated 
        during the SCF procedure. Only use after SCF procedure is complete.

        Arguments:
            convertToAO: Boolean, optional, default False
                If True, converts the density matrix from MO space to AO space via D_{AO} = 2 S^{-1}D_{MO}
        
        Returns:
            current_dm: 2D array
                current density matrix
        '''
        if convertToAO:
            return 2 * numpy.linalg.inv(self.mf.get_ovlp()) @ self.current_dm
        return self.current_dm

    def set(self, current_dm=None, purgeSolvers=-1, convergence=-1, initScattering=-1, trustScaleRange=None, 
            memSize=-1, memScale=-1, mo_coeffs=None):
        if type(current_dm) is numpy.ndarray:
            self.setCurrentDm(current_dm)
        if purgeSolvers >= 0:
            self.purgeSolvers = purgeSolvers
        self.convergene_thresh = convergence
        self.init_scattering = initScattering
        self.subconverger_rm.trustScaleRange = trustScaleRange



    def kernel(self, purgeSolvers=0.5, convergence=8, initScattering=0.1, trustScaleRange=(0.01, 0.2, 8), 
            memScale=0.2, dm0=None):
        self._purgeSolvers = purgeSolvers
        self.convergence_thresh = 10**(-convergence)
        self.init_scattering = initScattering
        self.subconverger_rm.trust_scale_range = trustScaleRange 
        self.mem_scale = memScale
        
        if type(dm0) is numpy.ndarray:
            self.initDensityMatrixDirectly(dm0)
        else:
            raise Exception('Illegal initial matrix: dm0 is not a numpy.ndarray.')

        return self.converge()


    def converge(self):
        '''
        Starts the SCF procedure. 

        Returns:
            scf_conv: boolean
                Whether the SCF managed to converge within the set amount of cycles to the given precision.
            final_energy: float
                Total SCF energy of the converged solution.
            final_mo_energy: 1D array
                Orbital energies of the converged MOs.
            final_mo_coeffs: 2D array
                MO coefficient matrix of the converged MOs.
            mo_occs: 1D array
                Absolute occupancies of the converged MOs.

        Examples:
        >>> mol = gto.M('H 0.0 0.0 0.0; F 0.0 0.0 1.0', basis='6-31g')
        >>> mf = scf.RHF(mol)
        >>> threads = 5
        >>> m3 = scf.M3SOSCF(mf, threads)
        >>> result = m3.converge()
        >>> log.info(result[1]) # Print SCF energy
        -99.9575044930158
        '''
        log = logger.new_logger(self.mf, self.mf.mol.verbose)

        if numpy.einsum('i->', self.mo_coeffs.flatten()) == 0:
            for sc in self.subconvergers:
                sc.setMoCoeffs(self.mo_basis_coeff)

        #basis = sigutils.getCanonicalBasis(len(self.current_dm[0]))

        self.subconvergers[0].setMoCoeffs(self.mo_basis_coeff)
        self.mo_coeffs[0,0] = self.mo_basis_coeff

        if self.threads >= 2:
            for j in range(1, self.threads):
                if self.method == 'uhf' or self.method == 'uks':
                    mo_pert_a = numpy.random.random(1)[0] * self.init_scattering * \
                            sigutils.vectorToMatrix(numpy.random.uniform(low=-0.5, high=0.5, 
                            size=(self.getDegreesOfFreedom(),)))
                    mo_pert_b = numpy.random.random(1)[0] * self.init_scattering * \
                            sigutils.vectorToMatrix(numpy.random.uniform(low=-0.5, high=0.5, 
                            size=(self.getDegreesOfFreedom(),)))
                    mo_coeffs_l = numpy.array((self.mo_basis_coeff[0] @ scipy.linalg.expm(mo_pert_a), 
                            self.mo_basis_coeff[1] @ scipy.linalg.expm(mo_pert_b)))
                    self.subconvergers[j].setMoCoeffs(mo_coeffs_l)
                    self.mo_coeffs[0,j] = mo_coeffs_l
                else:
                    mo_pert = numpy.random.random(1)[0] * self.init_scattering * \
                            sigutils.vectorToMatrix(numpy.random.uniform(low=-0.5, high=0.5, 
                            size=(self.getDegreesOfFreedom(),)))
                    mo_coeffs_l = self.mo_basis_coeff @ scipy.linalg.expm(mo_pert)
                    self.subconvergers[j].setMoCoeffs(mo_coeffs_l)
                    self.mo_coeffs[0,j] = mo_coeffs_l

        total_cycles = self.max_cycle
        final_energy = 0.0
        scf_conv = False
        final_mo_coeffs = None
        final_mo_energy = None

        s1e = self.mf.get_ovlp()
        h1e = self.mf.get_hcore()

        mo_occs = self.mf.mo_occ

        guess_energy = self.mf.energy_elec(self.mf.make_rdm1(self.mo_coeffs[0,0,:], mo_occs))[0]
        log.info("Guess energy: " + str(guess_energy))

        for cycle in range(self.max_cycle):

            # handle MO Coefficient cursor for I/O

            writeCursor = self.mo_cursor
            readCursor = self.mo_cursor-1
            if readCursor < 0:
                readCursor = len(self.mo_coeffs)-1

            if cycle == 0:
                writeCursor = 0
                readCursor = 0

            # edit subconverges according to solution density
            # a certain number of subconvergers get purged each iteration
            # purge = 0.3 - 0.8

            log.info("Iteration: " + str(cycle))

            purge_indices = None

            if cycle > 0 and len(self.subconvergers) > 1:
                sorted_indices = numpy.argsort(self.current_trusts[readCursor])
                purge_indices = sorted_indices[0:min(int(len(sorted_indices) * (self.purge_subconvergers)), 
                        len(sorted_indices))]
                uniquevals, uniqueindices = numpy.unique(self.current_trusts[readCursor], return_index=True)
                nonuniqueindices = []

                for i in range(self.threads):
                    if i not in uniqueindices:
                        nonuniqueindices.append(i)

                nui = numpy.array(nonuniqueindices, dtype=numpy.int32)
                zero_indices = numpy.where(self.current_trusts[readCursor,:] <= self.convergence_thresh)[0]
                purge_indices = numpy.unique(numpy.concatenate((purge_indices, nui, zero_indices)))
                purge_indices = numpy.sort(purge_indices)
                
                if purge_indices[0] == 0 and self.current_trusts[readCursor,0] > 0.0:
                    purge_indices = purge_indices[1:] 

                max_written = min(cycle, len(self.current_trusts))
                log.info("Purge Indices: " + str(purge_indices))
                log.info("Purging: " + str(len(purge_indices)) + " / " + str(len(self.subconvergers)))
                new_shifts = self.subconverger_rm.generateNewShifts(self.current_trusts[:max_written], 
                        self.mo_coeffs[:max_written], len(purge_indices), readCursor, log)

                for j in range(len(purge_indices)):
                    self.mo_coeffs[writeCursor,purge_indices[j]] = new_shifts[j]
                    self.current_energies[purge_indices[j]] = numpy.finfo(dtype=numpy.float32).max


            for j in range(len(self.subconvergers)):
                newCursor = readCursor
                if type(purge_indices) is numpy.ndarray:
                    if self.subconverger_indices[j] in purge_indices:
                        newCursor = writeCursor
                self.subconvergers[j].setMoCoeffs(self.mo_coeffs[newCursor,self.subconverger_indices[j]])


            # generate local solutions and trusts

            # buffer array for new mocoeffs
            newMoCoeffs = numpy.copy(self.mo_coeffs[readCursor])

            sorted_trusts = numpy.zeros(1, dtype=numpy.int32)
            if len(self.subconvergers) > 1:
                sorted_trusts = numpy.argsort(self.current_trusts[readCursor, 1:]) + 1

            for j in range(len(self.subconvergers)):

                sol, trust = self.subconvergers[j].getLocalSolAndTrust(h1e, s1e)

                numpy.set_printoptions(linewidth=500, precision=2)
                log.info("J: " + str(j) + " Trust: " + str(trust))

                if trust == 0:
                    continue

                writeTrustIndex = 0
                if j > 0:
                    writeTrustIndex = sorted_trusts[j-1]
                
                # update trust and solution array

                mc_threshold = 1 - self.current_trusts[readCursor,writeTrustIndex] + trust

                if j == 0 and self.current_trusts[readCursor,j] > 0.0 or len(self.subconvergers) == 1:
                    self.current_trusts[writeCursor,j] = trust
                    newMoCoeffs[j] = sol
                    self.subconverger_indices[j] = j


                elif numpy.random.rand(1) < mc_threshold:
                    self.current_trusts[writeCursor,writeTrustIndex] = trust
                    newMoCoeffs[writeTrustIndex] = sol
                    self.subconverger_indices[j] = writeTrustIndex

            # update moCoeff array with buffer
            self.mo_coeffs[writeCursor] = numpy.copy(newMoCoeffs)



            # check for convergence

            highestTrustIndex = numpy.argmax(self.current_trusts[writeCursor])
            log.info("Highest Trust Index: " + str(highestTrustIndex))
            log.info("Lowest Energy: " + str(numpy.min(self.current_energies)))
            log.info("Lowest Energy Index: " + str(numpy.argmin(self.current_energies)))
            # current energy

            for j in range(len(self.current_energies)):
                self.current_energies[j] = self.mf.energy_elec(self.mf.make_rdm1(self.mo_coeffs[writeCursor,j], 
                        self.mf.mo_occ))[0]
                log.info("ENERGY (" + str(j) + "): " + str(self.current_energies[j]))

            log.info("")

            scf_tconv =  1 - self.current_trusts[writeCursor,highestTrustIndex]**4 < self.convergence_thresh
            current_energy = numpy.min(self.current_energies)
            log.info("Lowest Energy: " + str(current_energy))
            if scf_tconv and current_energy - self.current_energies[highestTrustIndex] < -self.convergence_thresh:
                del_array1 = numpy.where(self.current_energies >= self.current_energies[highestTrustIndex])[0]
                del_array2 = numpy.where(1 - self.current_trusts[writeCursor,:]**4 < self.convergence_thresh)[0]
                log.info("Deletion Array 1 (Too High Energy): " + str(del_array1))
                log.info("Deletion Array 2 (Converged): " + str(del_array2))
                log.info("Intersected Deletion Array: " + str(numpy.intersect1d(del_array1, del_array2)))
                self.current_trusts[writeCursor,numpy.intersect1d(del_array1, del_array2)] = 0.0
                log.info("### DISREGARDING SOLUTION DUE TO NON VARIATIONALITY ###")
                scf_tconv = False

            log.info("Trust converged: " + str(scf_tconv))


            if scf_tconv:
                self.current_dm = self.mf.make_rdm1(self.mo_coeffs[writeCursor,highestTrustIndex], self.mf.mo_occ)

                final_fock = self.mf.get_fock(dm=self.current_dm, h1e=h1e, s1e=s1e)
                final_mo_coeffs = self.mo_coeffs[writeCursor,highestTrustIndex]
                final_mo_energy = self.calculateOrbitalEnergies(final_mo_coeffs, final_fock, s1e)
                final_energy = self.mf.energy_tot(self.current_dm, h1e=h1e)
                total_cycles = cycle+1
                
                self.mf.mo_energy = final_mo_energy
                self.mf.mo_coeff = final_mo_coeffs
                self.mf.e_tot = final_energy
                self.mf.converged = True

                scf_conv = True
                break

            self.mo_cursor += 1
            if self.mo_cursor >= len(self.mo_coeffs):
                self.mo_cursor = 0

        log.info("Final Energy: " + str(final_energy) + " ha")
        log.info("Cycles: " + str(total_cycles))
        
        self.dumpInfo(log, total_cycles)


        return scf_conv, final_energy, final_mo_energy, final_mo_coeffs, mo_occs
    

    def calculateOrbitalEnergies(self, mo_coefficients, fock, s1e):
        # Oribtal energies calculated from secular equation
        
        mo_energies = None
        
        if self.method == 'uhf' or self.method == 'uks':
            s1e_inv = numpy.linalg.inv(s1e)
            f_eff_a = s1e_inv @ fock[0]
            f_eff_b = s1e_inv @ fock[1]
            mo_energies_a = numpy.diag(numpy.linalg.inv(mo_coefficients[0]) @ f_eff_a @ mo_coefficients[0])
            mo_energies_b = numpy.diag(numpy.linalg.inv(mo_coefficients[1]) @ f_eff_b @ mo_coefficients[1])

            mo_energies = numpy.array((mo_energies_a, mo_energies_b))

        else:
            f_eff = numpy.linalg.inv(s1e) @ fock
            mo_energies = numpy.diag(numpy.linalg.inv(mo_coefficients) @ f_eff @ mo_coefficients)


        return mo_energies

    def dumpInfo(self, log, cycles):
        log.info("")
        log.info("==== INFO DUMP ====")
        log.info("")
        log.info("Number of Cycles:         " + str(cycles))
        log.info("Final Energy:             " + str(self.mf.e_tot))
        log.info("Converged:                " + str(self.mf.converged))
        aux_mol = pyscf.gto.M(atom=self.mf.mol.atom, basis=self.mf.mol.basis, spin=self.mf.mol.spin, 
                charge=self.mf.mol.charge, symmetry=1)
        log.info("Point group:              " + aux_mol.topgroup + " (Supported: " + aux_mol.groupname + ")")
        homo_index = None
        lumo_index = None
        if self.method == 'uhf' or self.method == 'uks':
            occs = numpy.where(self.mf.mo_occ[0,:] > 0.5)[0]
            no_occs = numpy.where(self.mf.mo_occ[0,:] < 0.5)[0]
            homo_index = (0, occs[numpy.argmax(self.mf.mo_energy[0,occs])])
            lumo_index = (0, no_occs[numpy.argmin(self.mf.mo_energy[0,no_occs])])
        else:
            occs = numpy.where(self.mf.mo_occ > 0.5)[0]
            no_occs = numpy.where(self.mf.mo_occ < 0.5)[0]
            homo_index = occs[numpy.argmax(self.mf.mo_energy[occs])]
            lumo_index = no_occs[numpy.argmin(self.mf.mo_energy[no_occs])]
        log.info("HOMO Index:               " + str(homo_index))
        log.info("LUMO Index:               " + str(lumo_index))
        homo_energy = 0
        lumo_energy = 0
        homo_energy = self.mf.mo_energy[homo_index]
        lumo_energy = self.mf.mo_energy[lumo_index]
        log.info("HOMO Energy:              " + str(homo_energy))
        log.info("LUMO Energy:              " + str(lumo_energy))
        log.info("Aufbau solution:          " + str(homo_energy < lumo_energy))

        if self.method == 'uhf' or self.method == 'uks':
            ss = self.mf.spin_square()
            log.info("Spin-Square:              " + str(ss[0]))
            log.info("Multiplicity:             " + str(ss[1]))

        irreps = ['-'] * len(self.mf.mo_energy[0])
        forced_irreps = False
        symm_overlap = numpy.ones(len(self.mf.mo_energy[0]))
        if self.method == 'uhf' or self.method == 'uks':
            irreps = [irreps, irreps]
            symm_overlap = [symm_overlap, symm_overlap]
        try:
            if self.method == 'uhf' or self.method == 'uks':
                irreps_a = pyscf.symm.addons.label_orb_symm(aux_mol, aux_mol.irrep_name, aux_mol.symm_orb, 
                        self.mf.mo_coeff[0])
                irreps_b = pyscf.symm.addons.label_orb_symm(aux_mol, aux_mol.irrep_name, aux_mol.symm_orb, 
                        self.mf.mo_coeff[1])
                if not (type(irreps_a) is type(None) or type(irreps_b) is type(None)):
                    irreps = [irreps_a, irreps_b]
            else:
                irreps1 = pyscf.symm.addons.label_orb_symm(aux_mol, aux_mol.irrep_name, aux_mol.symm_orb, 
                        self.mf.mo_coeff)
                if not type(irreps1) is type(None):
                    irreps = irreps1
        except:
            if self.method == 'uhf' or self.method == 'uks':
                mo_coeff_symm_a = pyscf.symm.addons.symmetrize_orb(aux_mol, self.mf.mo_coeff[0])
                mo_coeff_symm_b = pyscf.symm.addons.symmetrize_orb(aux_mol, self.mf.mo_coeff[1])
                symm_overlap_a = numpy.diag(mo_coeff_symm_a.conj().T @ self.mf.get_ovlp() @ self.mf.mo_coeff[0])
                symm_overlap_b = numpy.diag(mo_coeff_symm_b.conj().T @ self.mf.get_ovlp() @ self.mf.mo_coeff[1])
                irreps_a = pyscf.symm.addons.label_orb_symm(aux_mol, aux_mol.irrep_name, aux_mol.symm_orb, 
                        mo_coeff_symm_a)
                irreps_b = pyscf.symm.addons.label_orb_symm(aux_mol, aux_mol.irrep_name, aux_mol.symm_orb, 
                        mo_coeff_symm_b)
                if not (type(irreps_a) is type(None) or type(irreps_b) is type(None)):
                    irreps = [irreps_a, irreps_b]
                    forced_irreps = True
                    symm_overlap = numpy.array([symm_overlap_a, symm_overlap_b])
            else:
                mo_coeff_symm = pyscf.symm.addons.symmetrize_orb(aux_mol, self.mf.mo_coeff)
                symm_overlap = numpy.diag(mo_coeff_symm.conj().T @ self.mf.get_ovlp() @ self.mf.mo_coeff)
                irreps1 = pyscf.symm.addons.label_orb_symm(aux_mol, aux_mol.irrep_name, aux_mol.symm_orb, 
                        mo_coeff_symm)
                if not type(irreps1) is type(None):
                    irreps = irreps1
                    forced_irreps = True

        log.info("")
        log.info("")
        log.info("ORIBTAL SUMMARY:")
        log.info("")
        log.info("Index:        Energy [ha]:                        Occupation:    Symmetry:")
        if self.method == 'uhf' or self.method == 'uks':
            for index in range(len(self.mf.mo_energy[0])):
                mo_e = self.mf.mo_energy[0,index]
                s = str(index) + " (A)" + (9 - len(str(index))) * " "
                if mo_e > 0:
                    s += " "
                s += str(mo_e)
                if mo_e > 0:
                    s += (36 - len(str(mo_e))) * " "
                else:
                    s += (37 - len(str(mo_e))) * " "
                if self.mf.mo_occ[0,index] > 0:
                    s += "A"
                s += (65 - len(s)) * " " + irreps[0][index] + (" (FORCED, Overlap: " + 
                        str(round(symm_overlap[0][index], 5)) + ")") * forced_irreps
                log.info(s)

                mo_e = self.mf.mo_energy[1,index]
                s = str(index) + " (B)" + (9 - len(str(index))) * " "
                if mo_e > 0:
                    s += " "
                s += str(mo_e)
                if mo_e > 0:
                    s += (36 - len(str(mo_e))) * " "
                else:
                    s += (37 - len(str(mo_e))) * " "
                if self.mf.mo_occ[1,index] > 0:
                    s += "  B"
                s += (65 - len(s)) * " " + irreps[1][index] + (" (FORCED, Overlap: " + 
                        str(round(symm_overlap[1][index], 5)) + ")") * forced_irreps
                log.info(s)
        else:
            for index in range(len(self.mf.mo_energy)):
                mo_e = self.mf.mo_energy[index]
                s = str(index) + (13 - len(str(index))) * " "
                if mo_e > 0:
                    s += " "
                s += str(mo_e)
                if mo_e > 0:
                    s += (36 - len(str(mo_e))) * " "
                else:
                    s += (37 - len(str(mo_e))) * " "
                if self.mf.mo_occ[index] > 0:
                    s += "A "
                if self.mf.mo_occ[index] > 1:
                    s += "B"
                s += (65 - len(s)) * " " + irreps[index] + (" (FORCED, Overlap: " + 
                        str(round(symm_overlap[index], 5)) +  ")") * forced_irreps

                log.info(s)

            



###

# GENERAL SUBCONVERGER/SLAVE. FUNCTIONS AS AN EVALUATOR OF TRUST AND LOCAL SOL, FROM WHICH SOL DENSITY IS CONSTRUCTED

###


class Subconverger:
    '''
    Subconverger class used in M3SOSCF. This class calculates local solutions via a local NR step, which are then returned to the Master M3SOSCF class together with a trust value and the local gradient. Instances of this class should not be created in the code and interaction with the code herein should only be performed via the superlevel M3SOSCF instance.

    Arguments:
        lc: M3SOSCF
            Master M3SOSCF class
    '''


    m3 = None
    mo_coeffs = None
    newton = None
    trust_scale = 0.0

    def __init__(self, lc):
        self.m3 = lc
       
        molv = self.m3.mf.mol.verbose
        self.m3.mf.mol.verbose = 0
        self.newton = newton_ah.newton(self.m3.mf)
        self.m3.mf.mol.verbose = molv
        self.newton.max_cycle = 1
        self.newton.verbose = 0
        self.trust_scale = 10**-4

        self.newton.max_stepsize = self.m3.nr_stepsize

    def getLocalSolAndTrust(self, h1e=None, s1e=None):
        '''
        This method is directly invoked by the master M3SOSCF. It solves the local NR step from the previously
        assigned base MO coefficients and returns the solution as well as a trust and the gradient.

        Arguments:
            h1e: 2D array, optional, default None
                Core Hamiltonian. Inclusion not necessary, but improves performance
            s1e: 2D array, optional, default None
                Overlap matrix. Inclusion not necessary, but improves performance

        Returns:
            sol: 2D array
                local solution as an anti-hermitian MO rotation matrix.
            egrad: 1D array
                local gradient calculated at the base MO coefficients and used in the NR step as a compressed vector 
                that can be expanded to an anti-hermitian matrix via contraction with the canonical basis.
            trust: float
                Trust value of the local solution, always between 0 and 1. 0 indicates that the solution is
                infinitely far away and 1 indicates perfect convergence.
        '''

        old_dm = self.m3.mf.make_rdm1(self.mo_coeffs, self.m3.mf.mo_occ) 
        esol, converged = self.solveForLocalSol()
        new_dm = self.m3.mf.make_rdm1(esol, self.m3.mf.mo_occ)

        trust = self.getTrust(old_dm, new_dm, converged)
       
        sol = esol
        return sol, trust


    def solveForLocalSol(self):
        '''
        TODO:
        This method directly solves the NR step. In the current implementation, this is performed via an SVD 
        transformation: An SVD is performed on the hessian, the gradient is transformed into the SVD basis and the 
        individual, uncoupled linear equations are solved. If the hessian singular value is below a threshold of 
        its maximum, this component of the solution is set to 0 to avoid singularities.

        Arguments:
            hess: 2D array
                Electronic Hessian
            grad: 1D array
                Electronic Gradient

        Returns:
            localSol: 1D array
                Local Solution to the NR step.

        '''

        mo_occ = self.m3.mf.mo_occ
        self.newton.kernel(mo_coeff=numpy.copy(self.mo_coeffs), mo_occ=mo_occ)
        new_mo_coeffs = self.newton.mo_coeff

        return new_mo_coeffs, self.newton.converged

    def setMoCoeffs(self, moCoeffs):
        '''
        This method should be used for overriding the MO coefficients the subconverger uses as a basis for the NR step.

        Arguments:
            moCoeffs: 2D array
                New MO coefficients
        '''
        self.mo_coeffs = moCoeffs

    def getMoCoeffs(self):
        '''
        Returns the current MO coefficients used as a basis for the NR step. Se getLocalSolAndTrust for the solution 
        to the NR step.

        Returns:
            moCoeffs: 2D array
                Stored MO coefficients
        '''
        return self.mo_coeffs

    def getTrust(self, dm0, dm1, converged):
        '''
        Calculates the trust of a given solution from the solution distance, gradient and difference in energy.

        Arguments:
            sol: 1D array
                local solution in the canonical basis
            grad: 1D array
                electronic gradient in the canonical basis
            denergy: float
                difference in energy to the last step, i. e. E(now) - E(last)

        Returns:
            trust: float
                The trust of the given solution.
        '''

        #if converged:
        #    return 1.0

        e1 = self.m3.mf.energy_elec(dm1)[0]
        e0 = self.m3.mf.energy_elec(dm0)[0]
        denergy = e1 - e0

        l = numpy.linalg.norm(dm1-dm0) * self.trust_scale
        return 1.0 / (l + 1.0)**2 * self.auxilliaryXi(denergy)


    def auxilliaryXi(self, x):
        '''
        Auxilliary function that is used to define the effects of energy difference on the trust.

        Arguments:
            x: float
                function input value (here: the difference in energy)
        
        Returns:
            y: float
                function output value (here: scalar effect on trust)
        '''
        if x <= 0:
            return 1
        return numpy.e**(-x)






###

# Manages Reassignment of Subconvergers via Trust densities during the SCF procedures

###



class SubconvergerReassigmentManager:
    '''
    This class regulates the reassignment of subconvergers after each iteration. If a subconverger is either 
    redundant or gives a solution with a low trust, it is reassigned to another place in the electronic phase space
    where it generates more valuable local solutions.

    Arguments: 
        lc: M3SOSCF
            master M3SOSCF instance
    '''


    m3 = None
    alpha = 5.0
    mem_scale = 0.2
    trust_scale_range = None

    def __init__(self, lc):
        self.m3 = lc
        self.trust_scale_range = (0.01, 0.2, 8)
        self.alpha = 1.0 / self.trust_scale_range[1]


    def generateNewShifts(self, trusts, sols, total, cursor, log):
        '''
        This method is directly invoked by the master M3SOSCF class at the start of each SCF iteration to generate 
        new, useful positions for the reassigned subconvergers.

        Arguments:
            trusts: 1D array
                Array of trusts of each subconverger. trusts[i] belongs to the same subconverger as sols[i]
            sols: 3D array
                Array of the current MO coefficents of each subconverger. sols[i] belongs to the same subconverger 
                as trusts[i]
            total: int
                Total number of subconvergers to be reassigned, therefore total number of new shifts that need to 
                be generated

        Returns:
            shifts: 3D array
                Array of new MO coefficient for each subconverger that is to be reassigned. The shape of shifts 
                is ( total, n, n )

        '''

        maxTrust = numpy.max(trusts.flatten())
        self.alpha = 1.0 / ((self.trust_scale_range[1] - self.trust_scale_range[0]) * \
                (1 - maxTrust)**(self.trust_scale_range[2]) + self.trust_scale_range[0])

        log.info("Current Trust Scaling: " + str(self.alpha))


        for i in range(len(trusts)):
            p = cursor - i
            p %= len(trusts)
            if p < 0:
                p += len(trusts)
            trusts[i] *= self.mem_scale**p

        trusts = self.flattenTrustMOArray(trusts)
        sols = self.flattenTrustMOArray(sols)


        trustmap = numpy.argsort(trusts)
        normed_trusts = trusts[trustmap] / numpy.einsum('i->', trusts)

        def inverseCDF(x):
            for i in range(len(normed_trusts)):
                if x < normed_trusts[i]:
                    return i
                x -= normed_trusts[i]
            return len(normed_trusts)-1


        selected_indices = numpy.zeros(total, dtype=numpy.int32)

        for i in range(len(selected_indices)):
            rand = numpy.random.random(1)
            selected_indices[i] = trustmap[inverseCDF(rand)]

        # generate Points in each trust region

        if self.m3.method == 'uhf' or self.m3.method == 'uks':
            shifts = numpy.zeros((total, 2, len(self.m3.current_dm[0]), len(self.m3.current_dm[0])))
        else:
            shifts = numpy.zeros((total, len(self.m3.current_dm[0]), len(self.m3.current_dm[0])))

        for i in range(len(selected_indices)):
            if self.m3.method == 'uhf' or self.m3.method == 'uks':
                p_a = self.genTrustRegionPoints(trusts[selected_indices[i]], 1)[0]
                p_b = self.genTrustRegionPoints(trusts[selected_indices[i]], 1)[0]
                shifts[i,0] = sols[selected_indices[i],0] @ scipy.linalg.expm(sigutils.vectorToMatrix(p_a))
                shifts[i,1] = sols[selected_indices[i],1] @ scipy.linalg.expm(sigutils.vectorToMatrix(p_b))
            else:
                p = self.genTrustRegionPoints(trusts[selected_indices[i]], 1)[0]
                shifts[i] = sols[selected_indices[i]] @ scipy.linalg.expm(sigutils.vectorToMatrix(p))

        return shifts

    def flattenTrustMOArray(self, array):
        if len(array) == 1:
            return array[0]
        if len(array.shape) == 2: # Trust array
            return array.flatten()
        else:
            molen = len(self.m3.current_dm[0])
            farray = numpy.zeros((len(array)*len(array[0]), molen, molen))
            for i in range(len(array)):
                for j in range(len(array[0])):
                    farray[i*len(array[0])+j] = array[i,j]

            return farray

    def genSpherePoint(self):
        '''
        This method generates random points on any dimensional unit sphere via the box inflation algorithm. This 
        results in approximately uniform point distribution, although slight accumulation at the projected corners 
        is possible.

        Returns:
            point: 1D array
                Random point on a sphere
        '''
        dim = self.m3.getDegreesOfFreedom()

        capt_dim = numpy.random.randint(0, dim, 1)[0]
        capt_sign = numpy.random.randint(0, 1, 1)[0] * 2 - 1

        sub_point = numpy.random.random(dim-1)

        index_correct = 0
        point = numpy.zeros(dim)

        for i in range(dim):
            if i == int(capt_dim):
                point[i] = capt_sign
                index_correct = 1
                continue
            point[i] = sub_point[i-index_correct]

        point /= numpy.linalg.norm(point)
        
        return point

    def genSpherePoints(self, num):
        '''
        Generate multiple random points on any dimensional sphere. See genSpherePoint for futher details.

        Arguments:
            num: int
                number of random points to be generated

        Returns:
            points: 2D array
                array of random points on the sphere.
        '''
        points = numpy.zeros((num, self.m3.getDegreesOfFreedom()))

        for i in range(len(points)):
            points[i] = self.genSpherePoint()

        return points

    def genTrustRegionPoints(self, trust, num):
        '''
        Generates random points in a specific trust region.

        Arguments:
            trust: float
                The trust of the trust region, in which the points should be generated
            num: int
                The number of points to be generated

        Returns:
            points: 2D array
                The random points generated
        '''
        def inverseCDF(x):
            # Exponential Distribution
            return numpy.log(1-x) / (- self.alpha * trust)
            # Gaussian Distribution
            #return scipy.special.erfinv(x) / (self.alpha * trust)


        radii = inverseCDF(numpy.random.rand(num))

        spoints = self.genSpherePoints(num)
        dpoints = numpy.zeros(spoints.shape)

        for i in range(len(dpoints)):
            dpoints[i] = spoints[i] * radii[i]

        return dpoints






