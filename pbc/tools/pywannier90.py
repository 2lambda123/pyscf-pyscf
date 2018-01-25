'''
pyWannier90: Wannier90 for PySCF
Hung Q. Pham
email: pqh3.14@gmail.com
'''

# This is the only place needed to be modified
# The path for the libwannier90 library
W90LIB = 'libwannier90-path'

import numpy as np
import scipy
import cmath, os
import pyscf.lib.parameters as param
from pyscf import lib
from pyscf.pbc import df
from pyscf.pbc.dft import gen_grid, numint
import sys 
sys.path.append(W90LIB)

import importlib
found = importlib.find_loader('libwannier90') is not None
if found == True:
	import libwannier90
else:
	print('WARNING: Check the installation of libwannier90 and its path in pyscf/pbc/tools/pywannier90.py')
	print('libwannier90 path: ' + W90LIB)
	
	
def angle(v1, v2):
	'''
	Return the angle (in radiant between v1 and v2 
	'''	
	
	v1 = np.asarray(v1)
	v2 = np.asarray(v2)	
	cosa = v1.dot(v2)/ np.linalg.norm(v1) / np.linalg.norm(v2)
	return np.arccos(cosa)

def transform(x_vec, z_vec):
	'''
	Construct a transformation matrix to transform r_vec to the new coordinate system defined by x_vec and z_vec
	'''
	
	x_vec = x_vec/np.linalg.norm(np.asarray(x_vec))
	z_vec = z_vec/np.linalg.norm(np.asarray(z_vec))	
	assert x_vec.dot(z_vec) == 0
	y_vec = np.cross(x_vec,z_vec)
	new = np.asarray([x_vec, y_vec, z_vec])
	original = np.asarray([[1,0,0],[0,1,0],[0,0,1]])
	
	tran_matrix = np.empty([3,3]) 
	for row in range(3):
		for col in range(3):
			tran_matrix[row,col] = np.cos(angle(original[row],new[col]))
			
	return tran_matrix.T
	
def general_grid(cell, grid = [50,50,50]):
	'''
	Generate a general grid for a cell object, this is different to the periodic PySCF grid object
	This general grid is used to generate the *.xsf file
	'''	
	ngrid = np.asarray(grid)
	qv = lib.cartesian_prod([np.arange(i) for i in ngrid])
	a_frac = np.einsum('i,ij->ij', 1./(ngrid - 1), cell.lattice_vectors())
	coords = np.dot(qv, a_frac)
    	
	return coords

def R_r(r_norm, r = 1, zona = 1):
	'''
	Radial functions used to compute \Theta_{l,m_r}(\theta,\phi)
	'''	
	
	if r == 1:
		R_r = 2 * zona**(3/2) * np.exp(-zona*r_norm)
	elif r == 2:
		R_r = 1 / 2 / np.sqrt(2) * zona**(3/2) * (2 - zona*r_norm) * np.exp(-zona*r_norm/2)	
	else:
		R_r = np.sqrt(4/27) * zona**(3/2) * (1 - 2*zona*r_norm/3 + 2*(zona**2)*(r_norm**2)/27) * np.exp(-zona*r_norm/3)			
		
	return R_r
	
def theta(func, cost, phi):
	'''
	Basic angular functions (s,p,d,f) used to compute \Theta_{l,m_r}(\theta,\phi)
	'''	
	if 	func == 's':							# s
		theta = 1 / np.sqrt(4 * np.pi) * np.ones([cost.shape[0]])
	elif func == 'pz':
		theta = np.sqrt(3 / 4 / np.pi) * cost
	elif func == 'px':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(3 / 4 / np.pi) * sint * np.cos(phi)
	elif func == 'py':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(3 / 4 / np.pi) * sint * np.sin(phi)	
	elif func == 'dz2': 
		theta = np.sqrt(5 / 16 / np.pi) * (3*cost**2 - 1)
	elif func == 'dxz':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(15 / 4 / np.pi) * sint * cost * np.cos(phi)
	elif func == 'dyz':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(15 / 4 / np.pi) * sint * cost * np.sin(phi)
	elif func == 'dx2-y2':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(15 / 16 / np.pi) * (sint**2) * np.cos(2*phi)
	elif func == 'pxy':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(15 / 16 / np.pi) * (sint**2) * np.sin(2*phi)
	elif func == 'fz3':	
		theta = np.sqrt(7) / 4 / np.sqrt(np.pi) * (5*cost**3 - 3*cost)
	elif func == 'fxz2':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(21) / 4 / np.sqrt(2*np.pi) * (5*cost**2 - 1) * sint * np.cos(phi)
	elif func == 'fyz2':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(21) / 4 / np.sqrt(2*np.pi) * (5*cost**2 - 1) * sint * np.sin(phi)
	elif func == 'fz(x2-y2)':
		sint = np.sqrt(1 - cost**2)		
		theta = np.sqrt(105) / 4 / np.sqrt(np.pi) * sint**2 * cost * np.cos(2*phi)
	elif func == 'fxyz':
		sint = np.sqrt(1 - cost**2)	
		theta = np.sqrt(105) / 4 / np.sqrt(np.pi) * sint**2 * cost * np.sin(2*phi)	
	elif func == 'fx(x2-3y2)':
		sint = np.sqrt(1 - cost**2)	
		theta = np.sqrt(35) / 4 / np.sqrt(2*np.pi) * sint**3 * (np.cos(phi)**2 - 3*np.sin(phi)**2) * np.cos(phi)
	elif func == 'fy(3x2-y2)':
		sint = np.sqrt(1 - cost**2)	
		theta = np.sqrt(35) / 4 / np.sqrt(2*np.pi) * sint**3 * (3*np.cos(phi)**2 - np.sin(phi)**2) * np.sin(phi)
	
	return theta

def theta_lmr(l, mr, cost, phi):
	'''
	Compute the value of \Theta_{l,m_r}(\theta,\phi)
	ref: Table 3.1 and 3.2 of Chapter 3, wannier90 User Guide
	'''
	assert l in [0,1,2,3,-1,-2,-3,-4,-5]
	assert mr in [1,2,3,4,5,6,7]
	
	if 	l == 0:							# s
		theta_lmr = theta('s', cost, phi)
	elif (l == 1) and (mr == 1): 		# pz
		theta_lmr = theta('pz', cost, phi)
	elif (l == 1) and (mr == 2): 		# px
		theta_lmr = theta('px', cost, phi)
	elif (l == 1) and (mr == 3): 		# py
		theta_lmr = theta('py', cost, phi)
	elif (l == 2) and (mr == 1): 		# dz2	
		theta_lmr = theta('dz2', cost, phi)
	elif (l == 2) and (mr == 2): 		# dxz
		theta_lmr = theta('dxz', cost, phi)
	elif (l == 2) and (mr == 3): 		# dyz
		theta_lmr = theta('dyz', cost, phi)
	elif (l == 2) and (mr == 4): 		# dx2-y2
		theta_lmr = theta('dx2-y2', cost, phi)
	elif (l == 2) and (mr == 5): 		# pxy
		theta_lmr = theta('pxy', cost, phi)
	elif (l == 3) and (mr == 1): 		# fz3	
		theta_lmr = theta('fz3', cost, phi)
	elif (l == 3) and (mr == 2): 		# fxz2
		theta_lmr = theta('fxz2', cost, phi)
	elif (l == 3) and (mr == 3): 		# fyz2
		theta_lmr = theta('fyz2', cost, phi)
	elif (l == 3) and (mr == 4): 		# fz(x2-y2)
		theta_lmr = theta('fz(x2-y2)', cost, phi)
	elif (l == 3) and (mr == 5): 		# fxyz
		theta_lmr = theta('fxyz', cost, phi)
	elif (l == 3) and (mr == 6): 		# fx(x2-3y2)
		theta_lmr = theta('fx(x2-3y2)', cost, phi)
	elif (l == 3) and (mr == 7): 		# fy(3x2-y2)
		theta_lmr = theta('fy(3x2-y2)', cost, phi)
	elif (l == -1) and (mr == 1): 		# sp-1
		theta_lmr = 1/np.sqrt(2) * (theta('s', cost, phi) + theta('px', cost, phi))
	elif (l == -1) and (mr == 2): 		# sp-2
		theta_lmr = 1/np.sqrt(2) * (theta('s', cost, phi) - theta('px', cost, phi))	
	elif (l == -2) and (mr == 1): 		# sp2-1
		theta_lmr = 1/np.sqrt(3) * theta('s', cost, phi) - 1/np.sqrt(6) *theta('px', cost, phi) + 1/np.sqrt(2) * theta('py', cost, phi)
	elif (l == -2) and (mr == 2): 		# sp2-2	
		theta_lmr = 1/np.sqrt(3) * theta('s', cost, phi) - 1/np.sqrt(6) *theta('px', cost, phi) - 1/np.sqrt(2) * theta('py', cost, phi)	
	elif (l == -2) and (mr == 3): 		# sp2-3
		theta_lmr = 1/np.sqrt(3) * theta('s', cost, phi) + 2/np.sqrt(6) *theta('px', cost, phi)
	elif (l == -3) and (mr == 1): 		# sp3-1
		theta_lmr = 1/2 * (theta('s', cost, phi) + theta('px', cost, phi) + theta('py', cost, phi) + theta('pz', cost, phi))
	elif (l == -3) and (mr == 2): 		# sp3-2	
		theta_lmr = 1/2 * (theta('s', cost, phi) + theta('px', cost, phi) - theta('py', cost, phi) - theta('pz', cost, phi))	
	elif (l == -3) and (mr == 3): 		# sp3-3
		theta_lmr = 1/2 * (theta('s', cost, phi) - theta('px', cost, phi) + theta('py', cost, phi) - theta('pz', cost, phi))
	elif (l == -3) and (mr == 4): 		# sp3-4
		theta_lmr = 1/2 * (theta('s', cost, phi) - theta('px', cost, phi) - theta('py', cost, phi) + theta('pz', cost, phi))
	elif (l == -4) and (mr == 1): 		# sp3d-1
		theta_lmr = 1/np.sqrt(3) * theta('s', cost, phi) - 1/np.sqrt(6) *theta('px', cost, phi) + 1/np.sqrt(2) * theta('py', cost, phi)	
	elif (l == -4) and (mr == 2): 		# sp3d-2	
		theta_lmr = 1/np.sqrt(3) * theta('s', cost, phi) - 1/np.sqrt(6) *theta('px', cost, phi) - 1/np.sqrt(2) * theta('py', cost, phi)		
	elif (l == -4) and (mr == 3): 		# sp3d-3
		theta_lmr = 1/np.sqrt(3) * theta('s', cost, phi) + 2/np.sqrt(6) * theta('px', cost, phi)
	elif (l == -4) and (mr == 4): 		# sp3d-4
		theta_lmr = 1/np.sqrt(2) (theta('pz', cost, phi) + theta('dz2', cost, phi))
	elif (l == -4) and (mr == 5): 		# sp3d-5
		theta_lmr = 1/np.sqrt(2) (-theta('pz', cost, phi) + theta('dz2', cost, phi))
	elif (l == -5) and (mr == 1): 		# sp3d2-1
		theta_lmr = 1/np.sqrt(6) * theta('s', cost, phi) - 1/np.sqrt(2) *theta('px', cost, phi) - 1/np.sqrt(12) *theta('dz2', cost, phi) \
					+ 1/2 *theta('dx2-y2', cost, phi)
	elif (l == -5) and (mr == 2): 		# sp3d2-2	
		theta_lmr = 1/np.sqrt(6) * theta('s', cost, phi) + 1/np.sqrt(2) *theta('px', cost, phi) - 1/np.sqrt(12) *theta('dz2', cost, phi) \
					+ 1/2 *theta('dx2-y2', cost, phi)	
	elif (l == -5) and (mr == 3): 		# sp3d2-3
		theta_lmr = 1/np.sqrt(6) * theta('s', cost, phi) - 1/np.sqrt(2) *theta('py', cost, phi) - 1/np.sqrt(12) *theta('dz2', cost, phi) \
					- 1/2 *theta('dx2-y2', cost, phi)
	elif (l == -5) and (mr == 4): 		# sp3d2-4
		theta_lmr = 1/np.sqrt(6) * theta('s', cost, phi) + 1/np.sqrt(2) *theta('py', cost, phi) - 1/np.sqrt(12) *theta('dz2', cost, phi) \
					- 1/2 *theta('dx2-y2', cost, phi)
	elif (l == -5) and (mr == 5): 		# sp3d2-5
		theta_lmr = 1/np.sqrt(6) * theta('s', cost, phi) - 1/np.sqrt(2) *theta('pz', cost, phi) + 1/np.sqrt(3) *theta('dz2', cost, phi)
	elif (l == -5) and (mr == 6): 		# sp3d2-6
		theta_lmr = 1/np.sqrt(6) * theta('s', cost, phi) + 1/np.sqrt(2) *theta('pz', cost, phi) + 1/np.sqrt(3) *theta('dz2', cost, phi)	

	return theta_lmr
	

def g_r(grids_coor, site, l, mr, r, zona, x_axis = [1,0,0], z_axis = [0,0,1], unit = 'B'):
	'''
	Evaluate the projection function g(r) or \Theta_{l,m_r}(\theta,\phi) on a grid
	ref: Chapter 3, wannier90 User Guide
	Attributes:
		grids_coor					: a grids for the cell of interest
		site					: absolute coordinate (in Borh/Angstrom) of the g(r) in the cell
		l, mr					: l and mr value in the Table 3.1 and 3.2 of the ref
	Return:
		theta_lmr					: an array (ngrid, value) of g(r)

	'''

	unit_conv = 1
	if unit == 'A': unit_conv = param.BOHR
	
	
	r_vec = (grids_coor - site)		
	r_vec = np.einsum('iv,uv ->iu', r_vec, transform(x_axis, z_axis))
	r_norm = np.linalg.norm(r_vec,axis=1) 
	assert ( r_norm < 1e-8 ).any() == False			# Make sure r_norm is not too small, numerically instable
	cost = r_vec[:,2]/r_norm
	
	phi = np.empty_like(r_norm)
	for point in range(phi.shape[0]):
		if r_vec[point,0] > 1e-8:
			phi[point] = np.arctan(r_vec[point,1]/r_vec[point,0])
		elif r_vec[point,0] < -1e-8:
			phi[point] = np.arctan(r_vec[point,1]/r_vec[point,0]) + np.pi
		else:
			phi[point] = np.sign(r_vec[point,1]) * 0.5 * np.pi
	
	return theta_lmr(l, mr, cost, phi) * R_r(r_norm * unit_conv, r = r, zona = zona)
	
	
def get_ovlp(wA, wB, R_A = [0,0,0], R_B = [0,0,0]):
	'''
	Evaluate the overlap matrix between two Wannier functions obtained from two sets of Bloch orbitals
	Note: Wannier functions from the same set of Bloch orbitals are orthogonal to each other.
	Attributes:
		wA, wB		: two W90 objects
		RA, RB		: two R vectors
	Return:
		S_{AB} = \langle \omega_{n}^A(\mathbf{R_A,r}) | \omega_{l}^B(\mathbf{R_B,r}) \rangle
	'''	
	
	#R_vec = np.asarray(R).dot(self.cell.lattice_vectors())	
	cell = wA.cell
	kpts = wA.kmf.kpts
	num_kpts = kpts.shape[0]
	C = wA.kmf.mo_coeff_kpts
	U_wA = wA.U_matrix
	U_wB = wB.U_matrix
	# Be careful about R_A, R_B: should be the absolute vectors
	s = 0
	for k1_id in range(num_kpts):
		for k2_id in range(num_kpts):
			k_A = kpts[k1_id]
			k_B = kpts[k2_id]	
			C_A = C[k1_id]
			C_B = C[k2_id]	
			C_tildle_A = C_A.dot(U_wA[k1_id])
			C_tildle_B = C_B.dot(U_wB[k1_id])
			S_bloch = 0
			s =+  np.einsum('mk,nl,mn->kl', C_tildle_A, C_tildle_B, S_bloch, optimize=True) * np.exp(-1j * (k_B.dot(R_B) - k_A.dot(R_A)))
			
	return s/np.sqrt(num_kpts)
			
	
class W90:
	def __init__(self, kmf, mp_grid, num_wann, gamma = False, spinors = False, spin_up = None, other_keywords = None):
		
		self.kmf = kmf
		self.cell = kmf.cell
		self.num_wann = num_wann
		self.keywords = other_keywords

		# Collect the pyscf calculation info
		self.U = [scipy.linalg.sqrtm(s) for s in kmf.get_ovlp()]	# Used to orthonormalize the Bloch states
		self.num_bands_tot = self.cell.nao_nr()
		self.num_kpts_loc = kmf.kpts.shape[0]
		self.mp_grid_loc = mp_grid
		assert self.num_kpts_loc == np.asarray(self.mp_grid_loc).prod()
		self.real_lattice_loc = self.cell.lattice_vectors() * param.BOHR
		self.recip_lattice_loc = self.cell.reciprocal_vectors() / param.BOHR
		self.kpt_latt_loc = self.cell.get_scaled_kpts(kmf.kpts)
		self.num_atoms_loc = self.cell.natm
		self.atom_symbols_loc = [atom[0] for atom in self.cell._atom]
		self.atom_atomic_loc = [atom[0] for atom in self.cell._atm]
		self.atoms_cart_loc = np.asarray([(np.asarray(atom[1])* param.BOHR).tolist() for atom in self.cell._atom])
		lattice = np.sqrt((np.sum(self.cell.lattice_vectors()**2, axis = 1))) * param.BOHR
		self.atoms_frac_loc = self.atoms_cart_loc/lattice
		self.gamma_only, self.spinors = (0 , 0) 
		if gamma == True : self.gamma_only = 1
		if spinors == True : self.spinors = 1
		
		# Wannier90_setup outputs
		self.num_bands_loc = None 
		self.num_wann_loc = None 
		self.nntot_loc = None
		self.nn_list = None 
		self.proj_site = None
		self.proj_l = None
		proj_m = None
		self.proj_radial = None
		self.proj_z = None 
		self.proj_x = None
		self.proj_zona = None
		self.exclude_bands = None
		self.proj_s = None
		self.proj_s_qaxis = None
		
		# Input for Wannier90_run
		self.band_included_list = None
		self.A_matrix_loc = None
		self.M_matrix_loc = None 
		self.eigenvalues_loc = None 
		
		# Wannier90_run outputs
		self.U_matrix = None
		self.U_matrix_opt = None
		self.lwindow = None
		self.wann_centres = None
		self.wann_spreads = None
		self.spread = None
		
		# Others
		self.use_bloch_phases = False
		self.check_complex = False
		if spin_up != None:
			if spin_up == True:
				self.mo_energy_kpts = self.kmf.mo_energy_kpts[0]
				self.mo_coeff_kpts = self.kmf.mo_coeff_kpts[0]				
			else:
				self.mo_energy_kpts = self.kmf.mo_energy_kpts[1]
				self.mo_coeff_kpts = self.kmf.mo_coeff_kpts[1]			
		else:
			self.mo_energy_kpts = self.kmf.mo_energy_kpts
			self.mo_coeff_kpts = self.kmf.mo_coeff_kpts	
			
	def kernel(self):
		'''
		Main kernel for pyWannier90
		'''	
		self.make_win()
		self.setup()
		self.M_matrix_loc = self.get_M_mat()
		self.A_matrix_loc = self.get_A_mat()		
		self.eigenvalues_loc = self.get_epsilon_mat()	
		self.run()
	
	def make_win(self):
		'''
		Make a basic *.win file for wannier90
		'''		
		
		win_file = open('wannier90.win', "w")
		win_file.write('! Basic input\n')
		win_file.write('\n')
		win_file.write('num_bands       = %d\n' % (self.num_bands_tot))
		win_file.write('num_wann       = %d\n' % (self.num_wann))
		win_file.write('\n')		
		win_file.write('Begin Unit_Cell_Cart\n')				
		for row in range(3):
			win_file.write('%10.7f  %10.7f  %10.7f\n' % (self.real_lattice_loc[0, row], self.real_lattice_loc[1, row], \
			self.real_lattice_loc[2, row]))			
		win_file.write('End Unit_Cell_Cart\n')			
		win_file.write('\n')		
		win_file.write('Begin Atoms_Frac\n')			
		for atom in range(len(self.atom_symbols_loc)):
			win_file.write('%s  %7.7f  %7.7f  %7.7f\n' % (self.atom_symbols_loc[atom], self.atoms_frac_loc[atom][0], \
			 self.atoms_frac_loc[atom][1], self.atoms_frac_loc[atom][2]))			
		win_file.write('End Atoms_Frac\n')
		win_file.write('\n')
		if self.use_bloch_phases == True: win_file.write('use_bloch_phases = T\n\n')			
		if self.keywords != None: 
			win_file.write('!Additional keywords\n')
			win_file.write(self.keywords)
		win_file.write('\n\n\n')	
		win_file.write('mp_grid        = %d %d %d\n' % (self.mp_grid_loc[0], self.mp_grid_loc[1], self.mp_grid_loc[2]))	
		if self.gamma_only == 1: win_file.write('gamma_only : true\n')		
		win_file.write('begin kpoints\n')		
		for kpt in range(self.num_kpts_loc):
			win_file.write('%7.7f  %7.7f  %7.7f\n' % (self.kpt_latt_loc[kpt][0], self.kpt_latt_loc[kpt][1], self.kpt_latt_loc[kpt][2]))				
		win_file.write('End Kpoints\n')		
		win_file.close()
		
	def get_M_mat(self):
		'''
		Construct the ovelap matrix: M_{m,n}^{(\mathbf{k,b})}
		Equation (25) in MV, Phys. Rev. B 56, 12847
		'''	
		
		M_matrix_loc = np.empty([self.num_kpts_loc, self.nntot_loc, self.num_bands_loc, self.num_bands_loc], dtype = np.complex)
		
		for k_id in range(self.num_kpts_loc):
			for nn in range(self.nntot_loc):
					k1 = self.cell.get_abs_kpts(self.kpt_latt_loc[k_id])
					k_id2 = self.nn_list[nn, k_id, 0] - 1
					k2_ = self.kpt_latt_loc[k_id2]
					k2_scaled = k2_ + self.nn_list[nn, k_id, 1:4]
					k2 = self.cell.get_abs_kpts(k2_scaled)
					s_AO = df.ft_ao.ft_aopair(self.cell, -k1+k2, kpti_kptj=[k1,k2], q = np.zeros(3))[0]
					s_AO_ortho = np.einsum('iu,uv,vj->ij', (scipy.linalg.inv(self.U[k_id])).T.conj(), s_AO, (scipy.linalg.inv(self.U[k_id2])))
					Cm = self.mo_coeff_kpts[k_id][:,self.band_included_list]
					Cn = self.mo_coeff_kpts[k_id2][:,self.band_included_list]						
					M_matrix_loc[k_id, nn,:,:] = np.einsum('mu,vn,uv->mn', Cm.T.conj(), Cn, s_AO, optimize = True)
		
		return M_matrix_loc
		
	def get_A_mat(self):
		'''
		Construct the projection matrix: A_{m,n}^{\mathbf{k}}
		Equation (62) in MV, Phys. Rev. B 56, 12847 or equation (22) in SMV, Phys. Rev. B 65, 035109
		'''					
		
		A_matrix_loc = np.empty([self.num_kpts_loc, self.num_wann_loc, self.num_bands_loc], dtype = np.complex)
		
		if self.use_bloch_phases == True:
			for k_id in range(self.num_kpts_loc):
				Amn = np.zeros([self.num_wann_loc, self.num_bands_loc])
				np.fill_diagonal(Amn, 1)
				A_matrix_loc[k_id,:,:] = Amn
		else:		
			grids = gen_grid.UniformGrids(self.cell)
			grids.build()
			
			for k_id in range(self.num_kpts_loc):
				kpt = self.cell.get_abs_kpts(self.kpt_latt_loc[k_id])
				ao = numint.eval_ao(self.cell, grids.coords, kpt = kpt)
				for ith_wann in range(self.num_wann_loc):
					frac_site = self.proj_site[ith_wann] 
					abs_site = frac_site.dot(self.real_lattice_loc) / param.BOHR
					l = self.proj_l[ith_wann]
					mr = self.proj_m[ith_wann]
					r = self.proj_radial[ith_wann]
					zona = self.proj_zona[ith_wann]
					x_axis = self.proj_x[ith_wann]
					z_axis = self.proj_z[ith_wann]
					gr = g_r(grids.coords, abs_site, l, mr, r, zona, x_axis, z_axis, unit = 'B')
					C = np.dot(self.U[k_id], self.mo_coeff_kpts[k_id])[:,self.band_included_list] 
					s_ao = np.einsum('i,iu,i->u', grids.weights, ao.conj(), gr, optimize = True)
					A_matrix_loc[k_id,ith_wann,:] = np.einsum('um,u->m', C, s_ao, optimize = True)
					
		return A_matrix_loc

	def get_epsilon_mat(self):
		'''
		Construct the eigenvalues matrix: \epsilon_{n}^(\mathbf{k})
		'''
			
		return np.asarray(self.mo_energy_kpts)[:,self.band_included_list] * param.HARTREE2EV

	def setup(self):
		'''
		Execute the Wannier90_setup
		'''
		
		seed__name = "wannier90"
		real_lattice_loc = self.real_lattice_loc.flatten()
		recip_lattice_loc = self.recip_lattice_loc.flatten()
		kpt_latt_loc = self.kpt_latt_loc.flatten()
		atoms_cart_loc = self.atoms_cart_loc.flatten()

		bands_wann_nntot, nn_list, proj_site, proj_l, proj_m, proj_radial, \
		proj_z, proj_x, proj_zona, exclude_bands, proj_s, proj_s_qaxis = \
					libwannier90.setup(seed__name, self.mp_grid_loc, self.num_kpts_loc, real_lattice_loc, \
					recip_lattice_loc, kpt_latt_loc, self.num_bands_tot, self.num_atoms_loc, \
					self.atom_atomic_loc, atoms_cart_loc, self.gamma_only, self.spinors) 
				
		# Convert outputs to the correct data type
		self.num_bands_loc, self.num_wann_loc, self.nntot_loc = np.int32(bands_wann_nntot)
		self.nn_list = np.int32(nn_list)
		self.proj_site = proj_site
		self.proj_l = np.int32(proj_l)
		self.proj_m = np.int32(proj_m)
		self.proj_radial = np.int32(proj_radial)
		self.proj_z = proj_z
		self.proj_x = proj_x
		self.proj_zona = proj_zona
		self.exclude_bands = np.int32(exclude_bands)
		self.band_included_list = [i for i in range(self.num_bands_tot) if (i + 1) not in self.exclude_bands]
		self.proj_s = np.int32(proj_s)
		self.proj_s_qaxis = proj_s_qaxis
		
	def run(self):
		'''
		Execute the Wannier90_run
		'''
		
		assert type(self.num_wann_loc) != None
		assert type(self.M_matrix_loc) == np.ndarray
		assert type(self.A_matrix_loc) == np.ndarray
		assert type(self.eigenvalues_loc) == np.ndarray
		 
		
		seed__name = "wannier90"	
		recip_lattice_loc = self.recip_lattice_loc.flatten()
		kpt_latt_loc = self.kpt_latt_loc.flatten()
		atoms_cart_loc = self.atoms_cart_loc.flatten()		
		real_lattice_loc = self.real_lattice_loc.flatten()	
		M_matrix_loc = self.M_matrix_loc.flatten()	
		A_matrix_loc = self.A_matrix_loc.flatten()	 
		eigenvalues_loc = self.eigenvalues_loc.flatten()			
		
		U_matrix, U_matrix_opt, lwindow, wann_centres, wann_spreads, spread = \
		libwannier90.run(seed__name, self.mp_grid_loc, self.num_kpts_loc, real_lattice_loc, \
							recip_lattice_loc, kpt_latt_loc, self.num_bands_tot, self.num_bands_loc, self.num_wann_loc, self.nntot_loc, self.num_atoms_loc, \
							self.atom_atomic_loc, atoms_cart_loc, self.gamma_only, \
							M_matrix_loc, A_matrix_loc, eigenvalues_loc)
							
		# Convert outputs to the correct data type
		self.U_matrix = U_matrix
		self.U_matrix_opt = U_matrix_opt
		lwindow = np.int32(lwindow.real)
		self.lwindow = (lwindow == 1)
		self.wann_centres = wann_centres.real
		self.wann_spreads = wann_spreads.real
		self.spread = spread.real
	
	def export_unk(self, grid = [50,50,50]):
		'''
		Export the periodic part of BF in a real space grid for plotting with wannier90
		'''	
		
		from scipy.io import FortranFile
		grids_coor = general_grid(self.cell, grid)	
		
		for k_id in range(self.num_kpts_loc):
			kpt = self.cell.get_abs_kpts(self.kpt_latt_loc[k_id])	
			ao = numint.eval_ao(self.cell, grids_coor, kpt = kpt)
			u_ao = np.einsum('x,xi->xi', np.exp(-1j*np.dot(grids_coor, kpt)), ao, optimize = True)
			unk_file = FortranFile('UNK0000' + str(k_id + 1) + '.1', 'w')
			unk_file.write_record(np.asarray([grid[0], grid[1], grid[2], k_id + 1, self.num_bands_loc], dtype = np.int32))	
			mo_included = np.dot(self.U[k_id], self.mo_coeff_kpts[k_id])[:,self.band_included_list]		
			u_mo = np.einsum('xi,in->xn', u_ao, mo_included, optimize = True)
			for band in range(len(self.band_included_list)):	
				unk_file.write_record(np.asarray(u_mo[:,band], dtype = np.complex))					
			unk_file.close()

	def export_AME(self, grid = [50,50,50]):
		'''
		Export A_{m,n}^{\mathbf{k}} and M_{m,n}^{(\mathbf{k,b})} and \epsilon_{n}^(\mathbf{k})
		'''	
		
		if self.A_matrix_loc.all() == None:
			self.make_win()
			self.setup()
			self.M_matrix_loc = self.get_M_mat()
			self.A_matrix_loc = self.get_A_mat()		
			self.eigenvalues_loc = self.get_epsilon_mat()
			self.export_unk(self, grid = grid)
			
		with open('wannier90.mmn', 'w') as f:
			f.write('Generated by the pyWannier90\n')		
			f.write('    %d    %d    %d\n' % (self.num_bands_loc, self.num_kpts_loc, self.nntot_loc))
	
			for k_id in range(self.num_kpts_loc):
				for nn in range(self.nntot_loc):
					k_id1 = k_id + 1
					k_id2 = self.nn_list[nn, k_id, 0]
					nnn, nnm, nnl = self.nn_list[nn, k_id, 1:4]
					f.write('    %d  %d    %d  %d  %d\n' % (k_id1, k_id2, nnn, nnm, nnl))
					for m in range(self.num_bands_loc):
						for n in range(self.num_bands_loc):
							f.write('    %22.18f  %22.18f\n' % (self.M_matrix_loc[k_id, nn,m,n].real, self.M_matrix_loc[k_id, nn,m,n].imag))
					
	
		with open('wannier90.amn', 'w') as f:
			f.write('    %d\n' % (self.num_bands_loc*self.num_kpts_loc*self.num_wann_loc))		
			f.write('    %d    %d    %d\n' % (self.num_bands_loc, self.num_kpts_loc, self.num_wann_loc))
	
			for k_id in range(self.num_kpts_loc):
				for ith_wann in range(self.num_wann_loc):
					for band in range(self.num_bands_loc):
						f.write('    %d    %d    %d    %22.18f    %22.18f\n' % (band+1, ith_wann+1, k_id+1, self.A_matrix_loc[k_id,ith_wann,band].real, self.A_matrix_loc[k_id,ith_wann,band].imag))
		
		with open('wannier90.eig', 'w') as f:
			for k_id in range(self.num_kpts_loc):
				for band in range(self.num_bands_loc):
						f.write('    %d    %d    %22.18f\n' % (band+1, k_id+1, self.eigenvalues_loc[k_id,band]))
			
	def get_wannier(self, grid = [50,50,50]):
		'''
		Evaluate the MLWF using a general grid
		'''	
		
		grids_coor = general_grid(self.cell, grid)
		
		WFs = 0
		
		for k_id in range(self.num_kpts_loc): #self.num_kpts_loc
			kpt = self.cell.get_abs_kpts(self.kpt_latt_loc[k_id])	
			ao = numint.eval_ao(self.cell, grids_coor, kpt = kpt)
			mo_included = np.dot(self.U[k_id], self.mo_coeff_kpts[k_id])[:,self.band_included_list]
			mo_in_window = self.lwindow[k_id]
			C_opt = mo_included[:,mo_in_window].dot(self.U_matrix_opt[k_id].T)
			C_tildle = C_opt.dot(self.U_matrix[k_id].T)			
			WFs = WFs + np.einsum('xi,in->xn', ao, C_tildle, optimize = True)
		
		# Fix the global phase following the pw2wannier90 procedure, todo: why?
		max_index = (WFs*WFs.conj()).real.argmax(axis=0)
		norm_wfs = np.diag(WFs[max_index,:])
		norm_wfs = norm_wfs/np.absolute(norm_wfs)
		WFs = WFs/norm_wfs/self.num_kpts_loc	
		
		# Check the 'reality' following the pw2wannier90 procedure
		for WF_id in range(self.num_wann_loc):
			ratio_max = np.abs(WFs[(WFs[:,WF_id].real > 0.01),WF_id].imag/WFs[(WFs[:,WF_id].real > 0.01),WF_id].real).max(axis=0)
			print('The maximum imag/real for wannier function ', WF_id,' : ', ratio_max)
		
		return WFs

	def plot_wf(self, outfile = 'MLWF', wf_list = None, supercell = [1,1,1], grid = [50,50,50]):
		'''
		Export Wannier function at cell R
		xsf format: http://web.mit.edu/xcrysden_v1.5.60/www/XCRYSDEN/doc/XSF.html
		Attributes:
			wf_list		: a list of MLWFs to plot
			supercell	: a supercell used for plotting
		'''	
		
		if wf_list == None: wf_list = list(range(self.num_wann_loc))
		from pyscf.pbc.tools import pbc

		super_cell = pbc.super_cell(self.cell,supercell)
		real_lattice_loc = super_cell.lattice_vectors() * param.BOHR
		atom_symbols_loc = [atom[0] for atom in super_cell._atom]
		atoms_cart_loc = np.asarray([(np.asarray(atom[1])* param.BOHR).tolist() for atom in super_cell._atom])
		num_atoms_loc = super_cell.natm		
		nx, ny, nz = np.asarray(grid)
		nX, nY, nZ = tuple((np.asarray(grid)-1)*np.asarray(supercell) + 1)
		superWF = np.empty([nX, nY, nZ])
		superWF_temp = np.empty([nX, nY, nZ])
		WFs = self.get_wannier(grid = grid)

		
		for wf_id in wf_list:
			assert wf_id in list(range(self.num_wann_loc))
			WF = WFs[:,wf_id].reshape(nx,ny,nz).real
				
			for x in range(supercell[0]):
				for y in range(supercell[1]):
					for z in range(supercell[2]):					
						superWF_temp[:nx,:ny,((nz-1)*z):((nz-1)*z + nz)] = WF
					superWF_temp[:,((ny-1)*y):((ny-1)*y + ny),:] = superWF_temp[:,:ny,:]
				superWF[((nx-1)*x):((nx-1)*x + nx),:,:] = superWF_temp[:nx,:,:]
				
					
			with open(outfile + '-' + str(wf_id) + '.xsf', 'w') as f:
				f.write('Generated by the pyWannier90\n\n')		
				f.write('CRYSTAL\n')
				f.write('PRIMVEC\n')	
				for row in range(3):
					f.write('%10.7f  %10.7f  %10.7f\n' % (real_lattice_loc[0, row], real_lattice_loc[1, row], \
					real_lattice_loc[2, row]))	
				f.write('PRIMVEC\n')
				for row in range(3):
					f.write('%10.7f  %10.7f  %10.7f\n' % (real_lattice_loc[0, row], real_lattice_loc[1, row], \
					real_lattice_loc[2, row]))	
				f.write('PRIMCOORD\n')
				f.write('%3d %3d\n' % (num_atoms_loc, 1))
				for atom in range(len(atom_symbols_loc)):
					f.write('%s  %7.7f  %7.7f  %7.7f\n' % (atom_symbols_loc[atom], atoms_cart_loc[atom][0], \
					 atoms_cart_loc[atom][1], atoms_cart_loc[atom][2]))				
				f.write('\n\n')			
				f.write('BEGIN_BLOCK_DATAGRID_3D\n3D_field\nBEGIN_DATAGRID_3D_UNKNOWN\n')	
				f.write('   %5d	 %5d  %5d\n' % (nX, nY, nZ))		
				f.write('   %10.7f  %10.7f  %10.7f\n' % tuple(np.zeros(3).tolist()))
				for row in range(3):
					f.write('   %10.7f  %10.7f  %10.7f\n' % (real_lattice_loc[0, row], real_lattice_loc[1, row], \
					real_lattice_loc[2, row]))	
					
				fmt = ' %13.5e' * nX + '\n'
				for iz in range(nZ):
					for iy in range(nY):
						f.write(fmt % tuple(superWF[:,iy,iz].tolist()))		
	
				f.write('\n')									
				f.write('END_DATAGRID_3D\nEND_BLOCK_DATAGRID_3D')		

	def plot_gr(self, outfile = 'MLWF', l = 0, mr = 1, r = 1, zona = 1, site = [0.5,0.5,0.5], x_axis = [1,0,0], z_axis = [0,0,1], grid = [50,50,50]):
		'''
		Export the g(r) function
		'''
		
		grids_coor = general_grid(self.cell, grid)
		nx, ny, nz = np.asarray(grid)
		abs_site = np.asarray(site).dot(self.real_lattice_loc) / param.BOHR		
		gr = g_r(grids_coor, abs_site, l, mr, r, zona, x_axis = x_axis, z_axis = z_axis, unit = 'A')
		gr = gr.reshape(nx,ny,nz)
		
		with open(outfile + '.xsf', 'w') as f:
			f.write('CRYSTAL\n')
			f.write('PRIMVEC\n')	
			for row in range(3):
				f.write('%10.7f  %10.7f  %10.7f\n' % (self.real_lattice_loc[0, row], self.real_lattice_loc[1, row], \
				self.real_lattice_loc[2, row]))	
			f.write('PRIMVEC\n')
			for row in range(3):
				f.write('%10.7f  %10.7f  %10.7f\n' % (self.real_lattice_loc[0, row], self.real_lattice_loc[1, row], \
				self.real_lattice_loc[2, row]))	
			f.write('PRIMCOORD\n')
			f.write('%3d %3d\n' % (self.num_atoms_loc, 1))
			for atom in range(len(self.atom_symbols_loc)):
				f.write('%s  %7.7f  %7.7f  %7.7f\n' % (self.atom_symbols_loc[atom], self.atoms_cart_loc[atom][0], \
				 self.atoms_cart_loc[atom][1], self.atoms_cart_loc[atom][2]))				
			f.write('\n\n')			
			f.write('BEGIN_BLOCK_DATAGRID_3D\n3D_field\nBEGIN_DATAGRID_3D_UNKNOWN\n')	
			f.write('   %5d	 %5d  %5d\n' % (nx, ny, nz))			
			f.write('   %10.7f  %10.7f  %10.7f\n' % tuple(np.zeros(3).tolist()))
			for row in range(3):
				f.write('   %10.7f  %10.7f  %10.7f\n' % (self.real_lattice_loc[0, row], self.real_lattice_loc[1, row], \
				self.real_lattice_loc[2, row]))				
			fmt = ' %13.5e' * nx + '\n'
			for iz in range(nz):
				for iy in range(ny):
					f.write(fmt % tuple(gr[:,iy,iz].tolist()))
			f.write('END_DATAGRID_3D\nEND_BLOCK_DATAGRID_3D')							

			
if __name__ == '__main__':
	import numpy as np
	from pyscf import scf, gto
	from pyscf.pbc import gto as pgto
	from pyscf.pbc import scf as pscf
	import pywannier90

	cell = pgto.Cell()
	cell.atom = '''
	 C                  3.17500000    3.17500000    3.17500000
	 H                  2.54626556    2.54626556    2.54626556
	 H                  3.80373444    3.80373444    2.54626556
	 H                  2.54626556    3.80373444    3.80373444
	 H                  3.80373444    2.54626556    3.80373444
	'''
	cell.basis = 'sto-3g'
	cell.a = np.eye(3) * 6.35
	cell.gs = [15] * 3
	cell.verbose = 5
	cell.build()


	nk = [1, 1, 1]
	abs_kpts = cell.make_kpts(nk)
	kmf = pscf.KRHF(cell, abs_kpts).mix_density_fit()
	ekpt = kmf.run()
		
	num_wann = 4
	keywords = \
	'''
	begin projections
	C:sp3
	end projections
	'''
	
	w90 = pywannier90.W90(kmf, nk, num_wann, other_keywords = keywords)
	w90.kernel()
	
	# Plotting using pyWannier90
	w90.plot_wf(wf_list=[0,1,2,3], supercell = [1,1,1])
	
	# Plotting using Wannier90
	w90.export_unk()
	keywords = \
	'''
	begin projections
	C:sp3
	end projections
	wannier_plot = True
	wannier_plot_supercell = 1
	'''

	w90 = pywannier90.W90(kmf, nk, num_wann, other_keywords = keywords)
	w90.kernel()