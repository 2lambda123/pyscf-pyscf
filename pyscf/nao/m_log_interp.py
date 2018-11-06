from __future__ import print_function, division
import numpy as np
from timeit import default_timer as timer

#import numba as nb
#@nb.jit(nopython=True)
#def sum6_nb(ff, r2k, ir2cc, fr2v):
#  for j in range(6): fr2v+=ff[...,r2k+j]*ir2cc[j]
#  return fr2v


#
#
#
def log_interp(ff, r, rho_min_jt, dr_jt):
  """
    Interpolation of a function given on the logarithmic mesh (see m_log_mesh how this is defined)
    6-point interpolation on the exponential mesh (James Talman)
    Args:
      ff : function values to be interpolated
      r  : radial coordinate for which we want intepolated value
      rho_min_jt : log(rr[0]), i.e. logarithm of minimal coordinate in the logarithmic mesh
      dr_jt : log(rr[1]/rr[0]) logarithmic step of the grid
    Result: 
      Interpolated value
    Example:
      nr = 1024
      rr,pp = log_mesh(nr, rmin, rmax, kmax)
      rho_min, dr = log(rr[0]), log(rr[1]/rr[0])
      y = interp_log(ff, 0.2, rho, dr)
  """
  if r<np.exp(rho_min_jt)/2: return ff[0] #  here should be less than or equal gg[0]

  lr = np.log(r)
  k=int((lr-rho_min_jt)/dr_jt)
  nr = len(ff)
  k = min(max(k,2), nr-4)
  dy=(lr-rho_min_jt-k*dr_jt)/dr_jt

  fv = (-dy*(dy**2-1.0)*(dy-2.0)*(dy-3.0)*ff[k-2] 
       +5.0*dy*(dy-1.0)*(dy**2-4.0)*(dy-3.0)*ff[k-1] 
       -10.0*(dy**2-1.0)*(dy**2-4.0)*(dy-3.0)*ff[k]
       +10.0*dy*(dy+1.0)*(dy**2-4.0)*(dy-3.0)*ff[k+1]
       -5.0*dy*(dy**2-1.0)*(dy+2.0)*(dy-3.0)*ff[k+2]
       +dy*(dy**2-1.0)*(dy**2-4.0)*ff[k+3])/120.0 

  return fv

#
#
#
def comp_coeffs_(self, r, i2coeff):
  """
    Interpolation of a function given on the logarithmic mesh (see m_log_mesh how this is defined)
    6-point interpolation on the exponential mesh (James Talman)
    Args:
      r  : radial coordinate for which we want the intepolated value
    Result: 
      Array of weights to sum with the functions values to obtain the interpolated value coeff
      and the index k where summation starts sum(ff[k:k+6]*coeffs)
  """
  if r<self.gg[0]/2:
    i2coeff.fill(0.0)
    i2coeff[0] = 1
    return 0

  lr = np.log(r)
  k  = int((lr-self.gammin_jt)/self.dg_jt)
  k  = min(max(k,2), self.nr-4)
  dy = (lr-self.gammin_jt-k*self.dg_jt)/self.dg_jt
  
  i2coeff[0] =     -dy*(dy**2-1.0)*(dy-2.0)*(dy-3.0)/120.0
  i2coeff[1] = +5.0*dy*(dy-1.0)*(dy**2-4.0)*(dy-3.0)/120.0
  i2coeff[2] = -10.0*(dy**2-1.0)*(dy**2-4.0)*(dy-3.0)/120.0
  i2coeff[3] = +10.0*dy*(dy+1.0)*(dy**2-4.0)*(dy-3.0)/120.0
  i2coeff[4] = -5.0*dy*(dy**2-1.0)*(dy+2.0)*(dy-3.0)/120.0
  i2coeff[5] =      dy*(dy**2-1.0)*(dy**2-4.0)/120.0

  return k-2

#
#
#
def comp_coeffs(self, r):
  i2coeff = np.zeros(6)
  k = comp_coeffs_(self, r, i2coeff)
  return k,i2coeff



class log_interp_c():
  """    Interpolation of radial orbitals given on a log grid (m_log_mesh)  """
  def __init__(self, gg):
    """
      gg: one-dimensional array defining a logarithmic grid
    """
    #assert(type(rr)==np.ndarray)
    assert(len(gg)>2)
    self.gg = gg
    self.nr = len(gg)
    self.gammin_jt = np.log(gg[0])
    self.dg_jt = np.log(gg[1]/gg[0])

  def __call__(self, ff, rr, rcut=None):
    """ Interpolation of vector data ff[...,:] and vector arguments rr[:] """
    assert ff.shape[-1]==self.nr
    ffa = ff.reshape(ff.size//self.nr, ff.shape[-1])
    if rcut is None: rcut = self.gg[-1]
    if type(rr)!=np.ndarray:
      rra = np.array([rr])
    else:
      rra = rr
    
    #print(__name__, type(rra), rra.shape, ffa.shape)
    
    #t0 = timer()
    r2l,r2k,ir2cc = self.coeffs_vv_rcut(rra, rcut)
    #t1 = timer()
    fr2v = np.zeros(ffa.shape[0:-1]+rra.shape[:])
    # print(__name__, fr2v.shape, fr2v[:,r2l[0]].shape, r2l[0].shape)
    for j in range(6): fr2v[:,r2l[0]]+= ffa[:,r2k+j]*ir2cc[j]
    #t2 = timer()
    #print(__name__, t1-t0, t2-t1)
    return fr2v.reshape(ff.shape[0:-1])


  def interp_v(self, ff, r):
    """ Interpolation right away """
    assert ff.shape[-1]==self.nr
    k,cc = comp_coeffs(self, r)
    result = np.zeros(ff.shape[0:-1])
    for j,c in enumerate(cc): result = result + c*ff[...,j+k]
    return result

  def interp_vv(self, ff, rr):
    """ Interpolation of vector data ff[...,:] and vector arguments rr[:] """
    assert ff.shape[-1]==self.nr
    r2k,ir2cc = self.coeffs_vv(rr)
    ifr2vv = np.zeros(tuple([6])+ff.shape[0:-1]+rr.shape[:])
    for j in range(6): ifr2vv[j,...] = ff[...,r2k+j]
    return np.einsum('if...,i...->f...', ifr2vv, ir2cc)

  def coeffs_vv(self, rr):
    """ Compute an array of interpolation coefficients (6, rr.shape) """
    ir2c = np.zeros(tuple([6])+rr.shape[:])

    lr = np.ma.log(rr)
    #print('lr', lr)
    r2k = np.zeros(rr.shape, dtype=np.int32)
    r2k[...] = (lr-self.gammin_jt)/self.dg_jt-2
    #print('r2r 1', r2k)
  
    r2k = np.where(r2k<0,0,r2k)
    r2k = np.where(r2k>self.nr-6,self.nr-6,r2k)
    hp = self.gg[0]/2
    r2k = np.where(rr<hp, 0, r2k)
    #print('r2k 2 ', r2k)
    
    dy = (lr-self.gammin_jt-(r2k+2)*self.dg_jt)/self.dg_jt
    #print('dy    ', dy)
  
    ir2c[0] = np.where(rr<hp, 1.0, -dy*(dy**2-1.0)*(dy-2.0)*(dy-3.0)/120.0)
    ir2c[1] = np.where(rr<hp, 0.0, +5.0*dy*(dy-1.0)*(dy**2-4.0)*(dy-3.0)/120.0)
    ir2c[2] = np.where(rr<hp, 0.0, -10.0*(dy**2-1.0)*(dy**2-4.0)*(dy-3.0)/120.0)
    ir2c[3] = np.where(rr<hp, 0.0, +10.0*dy*(dy+1.0)*(dy**2-4.0)*(dy-3.0)/120.0)
    ir2c[4] = np.where(rr<hp, 0.0, -5.0*dy*(dy**2-1.0)*(dy+2.0)*(dy-3.0)/120.0)
    ir2c[5] = np.where(rr<hp, 0.0, dy*(dy**2-1.0)*(dy**2-4.0)/120.0)
    #print('ir2c[0]    ', ir2c[0])
    #print('ir2c[1]    ', ir2c[1])
    return r2k,ir2c

  def coeffs_vv_rcut(self, rr, rcut):
    """ Compute an array of interpolation coefficients (6, rr.shape) """
    i2less = np.where(rr<rcut)
    rr_wh  = rr[i2less]
    ir2c = np.zeros(tuple([6])+rr_wh.shape[:])
    #print(__name__, i2less[0].shape)
    
    lr = np.ma.log(rr_wh)
    r2k = np.zeros(lr.shape, dtype=np.int32)
    r2k[...] = (lr-self.gammin_jt)/self.dg_jt-2
    #print(__name__, 'r2r 1', r2k)
  
    r2k = np.where(r2k<0,0,r2k)
    r2k = np.where(r2k>self.nr-6,self.nr-6,r2k)
    hp = self.gg[0]/2
    r2k = np.where(rr_wh<hp, 0, r2k)
    #print('r2k 2 ', r2k)
    
    dy = (lr-self.gammin_jt-(r2k+2)*self.dg_jt)/self.dg_jt
    #print('dy    ', dy)
  
    ir2c[0] = np.where(rr_wh<hp, 1.0, -dy*(dy**2-1.0)*(dy-2.0)*(dy-3.0)/120.0)
    ir2c[1] = np.where(rr_wh<hp, 0.0, +5.0*dy*(dy-1.0)*(dy**2-4.0)*(dy-3.0)/120.0)
    ir2c[2] = np.where(rr_wh<hp, 0.0, -10.0*(dy**2-1.0)*(dy**2-4.0)*(dy-3.0)/120.0)
    ir2c[3] = np.where(rr_wh<hp, 0.0, +10.0*dy*(dy+1.0)*(dy**2-4.0)*(dy-3.0)/120.0)
    ir2c[4] = np.where(rr_wh<hp, 0.0, -5.0*dy*(dy**2-1.0)*(dy+2.0)*(dy-3.0)/120.0)
    ir2c[5] = np.where(rr_wh<hp, 0.0, dy*(dy**2-1.0)*(dy**2-4.0)/120.0)
    #print('ir2c[0]    ', ir2c[0])
    #print('ir2c[1]    ', ir2c[1])
    #r2k_dense = np.zeros(rr.shape, dtype=np.int32)
    #ir2c_dense = np.zeros(tuple([6])+rr.shape[:])
    #r2k_dense[i2less] = r2k
    #for i in range(6): ir2c_dense[i,i2less] = ir2c[i,:]
    return i2less,r2k,ir2c

  coeffs=comp_coeffs
  """ Interpolation pointers and coefficients """
  
  def diff(self, za):
    """
      Return array with differential 
      za :  input array to be differentiated 
    """
    ar = self.gg
    dr = self.dg_jt
    nr = self.nr
    zb = np.zeros_like(za)
        
    zb[0]=(za[0]-za[1])/(ar[0]-ar[1]) # forward to improve
    zb[1]=(za[2]-za[0])/(ar[2]-ar[0]) # central? to improve
    zb[2]=(za[3]-za[1])/(ar[3]-ar[1]) # central? to improve 
    
    for i in range(3,nr-3):
      zb[i]=(45.0*(za[i+1]-za[i-1])-9.0*(za[i+2]-za[i-2])+za[i+3]-za[i-3])/(60.0*dr*ar[i])
    
    zb[nr-3]=(za[nr-1]-za[nr-5]+8.0*(za[nr-2]-za[nr-4]))/ ( 12.0*self.dg_jt*self.gg[nr-3] )
    zb[nr-2]=(za[nr-1]-za[nr-3])/(2.0*dr*ar[nr-2])
    zb[nr-1]=( 4.0*za[nr-1]-3.0*za[nr-2]+za[nr-3])/(2.0*dr*ar[nr-1] );
    return zb
    
#    Example:
#      loginterp =log_interp_c(rr)

if __name__ == '__main__':
  from pyscf.nao.m_log_interp import log_interp, log_interp_c, comp_coeffs_
  from pyscf.nao.m_log_mesh import log_mesh
  rr,pp = log_mesh(1024, 0.01, 20.0)
  interp_c = log_interp_c(rr)
  gc = 0.234450
  ff = np.array([np.exp(-gc*r**2) for r in rr])
  rho_min_jt, dr_jt = np.log(rr[0]), np.log(rr[1]/rr[0]) 
  for r in np.linspace(0.01, 25.0, 100):
    yref = log_interp(ff, r, rho_min_jt, dr_jt)
    k,coeffs = comp_coeffs(interp_c, r)
    y = sum(coeffs*ff[k:k+6])
    if(abs(y-yref)>1e-15): print(r, yref, y, np.exp(-gc*r**2))
