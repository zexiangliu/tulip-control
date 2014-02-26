# Copyright (c) 2011, 2012, 2013 by California Institute of Technology
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 
# 3. Neither the name of the California Institute of Technology nor
#    the names of its contributors may be used to endorse or promote
#    products derived from this software without specific prior
#    written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL CALTECH
# OR THE CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
# OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
""" 
Algorithms related to discretization of continuous dynamics.

See Also
========
L{find_controller}
"""
import logging
logger = logging.getLogger(__name__)

import warnings
import pprint
from copy import deepcopy

import numpy as np
from scipy import sparse as sp

from tulip import polytope as pc
from tulip import transys as trs
from tulip.hybrid import LtiSysDyn, PwaSysDyn
from .prop2partition import PropPreservingPartition, pwa_partition, part2convex
from .feasible import is_feasible, solve_feasible

class AbstractSwitched(object):
    """Abstraction of HybridSysDyn, with mode-specific and common info.
    
    Attributes:
    
      - ppp: merged partition, if any
          Preserves both propositions and dynamics
    
      - ts: common TS, if any
      
      - modes: dict of {mode: AbstractPwa}
      
      - ppp2modes: map from C{ppp.regions} to C{modes[mode].ppp.regions}
          of the form:
          
              {mode: list}
          
          where C{list} has same indices as C{ppp.regions} and
          elements in each C{list} are indices of regions in
          each C{modes[mode].ppp.regions}.
          
          type: dict
    
    Each partition corresponds to some mode.
    (for switched systems)
    
    In each mode a L{PwaSubSys} is active.
    """
    def __init__(
        self, ppp=None, ts=None, modes=None,
        ppp2modes=None
    ):
        if modes is None:
            modes = dict()
        
        self.ppp = ppp
        self.ts = ts
        self.modes = modes
        self.ppp2modes = ppp2modes
    
    def __str__(self):
        s = 'Abstraction of switched system\n'
        s += str('common PPP:\n') + str(self.ppp)
        s += str('common ts:\n') + str(self.ts)
        
        for mode, ab in self.modes.iteritems():
            s += 'mode: ' + str(mode)
            s += ', with abstraction:\n' + str(ab)
    
    def ppp2pwa(self, mode, i):
        """Return original L{Region} containing C{Region} C{i} in C{mode}.
        
        @param mode: key of C{modes}
        
        @param i: Region index in common partition C{ppp.regions}.
        
        @return: tuple C{(j, region)} of:
            
                - index C{j} of L{Region} and
                - L{Region} object
            
            in C{modes[mode].ppp.regions}
        """
        region_idx = self.ppp2modes[mode][i]
        
        ab = self.modes[mode]
        
        j = ab.ppp2pwa[region_idx]
        pwa_region = ab.pwa_ppp[j]
        return (j, pwa_region)
        
    def ppp2sys(self, mode, i):
        """Return index of active PWA subsystem in C{mode},
        
        @param mode: key of C{modes}
        
        @param i: Region index in common partition C{ppp.regions}.
        
        @return: tuple C{(j, subsystem)} of:
                
                - index C{j} of PWA C{subsystem}
                - L{LtiSysDyn} object C{subsystem}
        """
        region_idx = self.ppp2modes[mode][i]
        subsystem_idx = self.modes[mode].ppp2sys[region_idx]
        subsystem = self.modes[mode].pwa.list_subsys[subsystem_idx]
        return (subsystem_idx, subsystem)
    
    def plot(self, color_seed=None):
        # different partition per mode ?
        axs = []
        for mode, ab in self.modes.iteritems():
            ax = ab.plot(color_seed=color_seed)
            ax.set_title('Abstraction for mode: ' + str(mode))
            axs += [ax]
        
        #if isinstance(self.ts, dict):
        #    for ts in self.ts:
        #        ax = ts.plot()
        #        axs += [ax]
        return axs

class AbstractPwa(object):
    """Discrete abstraction of PWA dynamics, with attributes:
    
      - ppp: Partition into Regions.
          Each Region corresponds to
          a discrete state of the abstraction

          type: L{PropPreservingPartition}

      - ts: Finite transition system abstracting the continuous system.
          Each state corresponds to a Region in C{ppp.regions}.
          It can be fed into discrete synthesis algorithms.

          type: L{transys.OpenFTS}

      - ppp2ts: bijection between C{ppp.regions} and C{ts.states}.
          Has common indices with C{ppp.regions}.
          Elements are states in C{ts.states}.
          (usually each state is a str)

          type: list of states

      - pwa_ppp: partition preserving both:
            
            - propositions and
            - domains of PWA subsystems
          
          Used for non-conservative planning.
          If just L{LtiSysDyn}, then the only difference
          of C{pwa_ppp} from C{orig_ppp} is convexification.
          
          type: L{PropPreservingPartition}
      
      - ppp2pwa: map of C{ppp.regions} to C{pwa_ppp.regions}.
          Has common indices with C{ppp.regions}.
          Elements are indices in C{pwa_ppp.regions}.
          
          type: list of integers

      - ppp2sys: map of C{ppp.regions} to C{PwaSubSys.list_subsys}.
          Has common indices with C{ppp.regions}.
          Elements are indices of sub-systems in C{PwaSubSys.list_subsys}.
          
          Semantics: j-th sub-system is active in i-th Region,
              where C{j = ppp2pwa[i]}

          type: list of integers
      
      - orig_ppp: partition preserving only propositions
          i.e., agnostic of dynamics
          
          type: L{PropPreservingPartition}
      
      - ppp2orig: map of C{ppp.regions} to C{orig_ppp.regions}:
          Has common indices with C{ppp.regions}.
          Elements are indices in C{orig_ppp.regions}.

          type: list of integers

      - disc_params: parameters used in discretization that 
          should be passed to the controller refinement
          to ensure consistency

          type: dict
    
    If any of the above is not given,
    then it is initialized to None.
            
    Note1: There could be some redundancy in ppp and ofts,
        in that they are both decorated with propositions.
        This might be useful to keep each of 
        them as functional units on their own
        (possible to change later).
    
    Note2: The 'Pwa' in L{AbstractPwa} includes L{LtiSysDyn}
        as a special case.
    """
    def __init__(
        self, ppp=None, ts=None, ppp2ts=None,
        pwa=None, pwa_ppp=None, ppp2pwa=None, ppp2sys=None,
        orig_ppp=None, ppp2orig=None,
        disc_params=None
    ):
        if disc_params is None:
            disc_params = dict()
        
        self.ppp = ppp
        self.ts = ts
        self.ppp2ts = ppp2ts
        
        self.pwa = pwa
        self.pwa_ppp = pwa_ppp
        self.ppp2pwa = ppp2pwa
        self.ppp2sys = ppp2sys
        
        self.orig_ppp = orig_ppp
        self.ppp2orig = ppp2orig
        
        # original_regions -> pwa_ppp
        # ppp2orig -> ppp2pwa_ppp
        # ppp2pwa -> ppp2pwa_sys
        
        self.disc_params = disc_params
    
    def __str__(self):
        s = str(self.ppp)
        s += str(self.ts)
        
        s += 30 * '-' + '\n'
        
        s += 'Map PPP Regions ---> TS states:\n'
        s += self._ppp2other_str(self.ppp2ts) + '\n'
        
        s += 'Map PPP Regions ---> PWA PPP Regions:\n'
        s += self._ppp2other_str(self.ppp2pwa) + '\n'
        
        s += 'Map PPP Regions ---> PWA Subsystems:\n'
        s += self._ppp2other_str(self.ppp2sys) + '\n'
        
        s += 'Map PPP Regions ---> Original PPP Regions:\n'
        s += self._ppp2other_str(self.ppp2orig) + '\n'
        
        s += 'Discretization Options:\n\t'
        s += pprint.pformat(self.disc_params) +'\n'
        
        return s
    
    def _ppp2other_str(self, ppp2other):
        s = ''
        for i, other in enumerate(ppp2other):
            s += '\t\t' + str(i) + ' -> ' + str(other) + '\n'
        return s
    
    def _debug_str_(self):
        s = str(self.ppp)
        s += str(self.ts)
        
        s += '(PWA + Prop)-Preserving Partition'
        s += str(self.pwa_ppp)
        
        s += 'Original Prop-Preserving Partition'
        s += str(self.orig_ppp)
        return s
    
    def plot(self, color_seed=None):
        if self.ppp is None or self.ts is None:
            warnings.warn('Either ppp or ts is None.')
            return
        
        ax = self.ppp.plot(trans=self.ts, ppp2trans=self.ppp2ts)
        #ax = self.ts.plot()
        
        return ax

def discretize(
    part, ssys, N=10, min_cell_volume=0.1,
    closed_loop=True, conservative=False,
    max_num_poly=5, use_all_horizon=False,
    trans_length=1, remove_trans=False, 
    abs_tol=1e-7,
    plotit=False, save_img=False, cont_props=None,
    plot_every=1
):
    """Refine the partition and establish transitions
    based on reachability analysis.
    
    See Also
    ========
    L{prop2partition.pwa_partition}, L{prop2partition.part2convex}
    
    @param part: L{PropPreservingPartition} object
    @param ssys: L{LtiSysDyn} or L{PwaSysDyn} object
    @param N: horizon length
    @param min_cell_volume: the minimum volume of cells in the resulting
        partition.
    @param closed_loop: boolean indicating whether the `closed loop`
        algorithm should be used. default True.
    @param conservative: if true, force sequence in reachability analysis
        to stay inside starting cell. If false, safety
        is ensured by keeping the sequence inside a convexified
        version of the original proposition preserving cell.
    @param max_num_poly: maximum number of polytopes in a region to use in 
        reachability analysis.
    @param use_all_horizon: in closed loop algorithm: if we should look
        for reach- ability also in less than N steps.
    @param trans_length: the number of polytopes allowed to cross in a
        transition.  a value of 1 checks transitions
        only between neighbors, a value of 2 checks
        neighbors of neighbors and so on.
    @param remove_trans: if True, remove found transitions between
        non-neighbors.
    @param abs_tol: maximum volume for an "empty" polytope
    
    @param plotit: plot partitioning as it evolves
    @type plotit: boolean,
        default = False
    
    @param save_img: save snapshots of partitioning to PDF files,
        requires plotit=True
    @type save_img: boolean,
        default = False
    
    @param cont_props: continuous propositions to plot
    @type cont_props: list of L{Polytope}
    
    @rtype: L{AbstractPwa}
    """
    orig_ppp = part
    min_cell_volume = (min_cell_volume /np.finfo(np.double).eps
        *np.finfo(np.double).eps)
    
    ispwa = isinstance(ssys, PwaSysDyn)
    islti = isinstance(ssys, LtiSysDyn)
    
    if ispwa:
        (part, ppp2pwa, part2orig) = pwa_partition(ssys, part)
    else:
        part2orig = range(len(part))
    
    # Save original polytopes, require them to be convex 
    if conservative:
        orig_list = None
        orig = 0
        ppp2orig = part2orig
    else:
        (part, new2old) = part2convex(part) # convexify
        ppp2orig = [part2orig[i] for i in new2old]
        
        # map new regions to pwa subsystems
        if ispwa:
            ppp2pwa = [ppp2pwa[i] for i in new2old]
        
        remove_trans = False # already allowed in nonconservative
        orig_list = []
        for poly in part:
            if len(poly) == 0:
                orig_list.append(poly.copy())
            elif len(poly) == 1:
                orig_list.append(poly[0].copy())
            else:
                raise Exception("discretize: "
                    "problem in convexification")
        orig = range(len(orig_list))
    
    # Cheby radius of disturbance set
    # (defined within the loop for pwa systems)
    if islti:
        if len(ssys.E) > 0:
            rd = ssys.Wset.chebR
        else:
            rd = 0.
    
    # Initialize matrix for pairs to check
    IJ = part.adj.copy()
    IJ = IJ.todense()
    IJ = np.array(IJ)
    logger.info("\n Starting IJ: \n" + str(IJ) )
    
    # next line omitted in discretize_overlap
    IJ = reachable_within(trans_length, IJ,
                          np.array(part.adj.todense()) )
    
    # Initialize output
    num_regions = len(part)
    transitions = np.zeros(
        [num_regions, num_regions],
        dtype = int
    )
    sol = deepcopy(part.regions)
    adj = part.adj.copy()
    adj = adj.todense()
    adj = np.array(adj)
    
    # next 2 lines omitted in discretize_overlap
    if ispwa:
        subsys_list = list(ppp2pwa)
    else:
        subsys_list = None
    ss = ssys
    
    # init graphics
    if plotit:
        # here to avoid loading matplotlib unless requested
        try:
            from plot import plot_partition, plot_transition_arrow
        except Exception, e:
            logger.error(e)
            plot_partition = None
        
        try:
            import matplotlib.pyplot as plt
            plt.ion()
            fig, (ax1, ax2) = plt.subplots(1, 2)
            ax1.axis('scaled')
            ax2.axis('scaled')
            file_extension = 'png'
        except Exception, e:
            logger.error(e)
            plot_partition = None
        
    iter_count = 0
    
    # List of how many "new" regions
    # have been created for each region
    # and a list of original number of neighbors
    #num_new_reg = np.zeros(len(orig_list))
    #num_orig_neigh = np.sum(adj, axis=1).flatten() - 1
    
    # Do the abstraction
    while np.sum(IJ) > 0:
        ind = np.nonzero(IJ)
        # i,j swapped in discretize_overlap
        i = ind[1][0]
        j = ind[0][0]
        IJ[j, i] = 0
        si = sol[i]
        sj = sol[j]
        
        #num_new_reg[i] += 1
        #print(num_new_reg)
        
        if ispwa:
            ss = ssys.list_subsys[subsys_list[i]]
            if len(ss.E) > 0:
                rd, xd = pc.cheby_ball(ss.Wset)
            else:
                rd = 0.
        
        if conservative:
            # Don't use trans_set
            trans_set = None
        else:
            # Use original cell as trans_set
            trans_set = orig_list[orig[i]]
        
        S0 = solve_feasible(
            si, sj, ss, N, closed_loop,
            use_all_horizon, trans_set, max_num_poly
        )
        
        msg = '\n Working with states:\n\t'
        msg += str(i) +' (#polytopes = ' +str(len(si) ) +'), and:\n\t'
        msg += str(j) +' (#polytopes = ' +str(len(sj) ) +')\n\t'
            
        if ispwa:
            msg += 'with active subsystem: '
            msg += str(subsys_list[i]) + '\n\t'
            
        msg += 'Computed reachable set S0 with volume: '
        msg += str(S0.volume) + '\n'
        
        logger.info(msg)
        
        # isect = si \cap S0
        isect = si.intersect(S0)
        vol1 = isect.volume
        risect, xi = pc.cheby_ball(isect)
        
        # diff = si \setminus S0
        diff = si.diff(S0)
        vol2 = diff.volume
        rdiff, xd = pc.cheby_ball(diff)
        
        # We don't want our partitions to be smaller than the disturbance set
        # Could be a problem since cheby radius is calculated for smallest
        # convex polytope, so if we have a region we might throw away a good
        # cell.
        if (vol1 > min_cell_volume) and (risect > rd) and \
           (vol2 > min_cell_volume) and (rdiff > rd):
        
            # Make sure new areas are Regions and add proposition lists
            if len(isect) == 0:
                isect = pc.Region([isect], si.props)
            else:
                isect.props = si.props.copy()
        
            if len(diff) == 0:
                diff = pc.Region([diff], si.props)
            else:
                diff.props = si.props.copy()
        
            # replace si by intersection (single state)
            sol[i] = isect
            
            # cut difference into connected pieces
            difflist = pc.separate(diff)
            num_new = len(difflist)
            
            # add each piece, as a new state
            for region in difflist:
                sol.append(region)
                
                # keep track of PWA subsystems map to new states
                if ispwa:
                    subsys_list.append(subsys_list[i])
            n_cells = len(sol)
            new_idx = xrange(n_cells-1, n_cells-num_new-1, -1)
            
            # Update transition matrix
            transitions = np.pad(transitions, (0,num_new), 'constant')
            
            transitions[i, :] = np.zeros(n_cells)
            for r in new_idx:
                
                #transitions[:, r] = transitions[:, i]
                # All sets reachable from start are reachable from both part's
                # except possibly the new part
                transitions[i, r] = 0
                transitions[j, r] = 0            
            
            if i != j:
                # sol[j] is reachable from intersection of sol[i] and S0..
                transitions[j, i] = 1
            
            # Update adjacency matrix
            old_adj = np.nonzero(adj[i, :])[0]
            adj[i, :] = np.zeros([n_cells -num_new])
            adj[:, i] = np.zeros([n_cells -num_new])
            
            adj = np.pad(adj, (0,num_new), 'constant')
            
            for r in new_idx:
                adj[i, r] = 1
                adj[r, i] = 1
                adj[r, r] = 1
                
                if not conservative:
                    orig = np.hstack([orig, orig[i]])
            adj[i, i] = 1
                        
            if logger.getEffectiveLevel() >= logging.INFO:
                msg = '\n Adding states ' + str(i) + ' and '
                for r in new_idx:
                    msg += str(r) + ' and '
                msg += '\n'
                        
            for k in np.setdiff1d(old_adj, [i,n_cells-1]):
                # Every "old" neighbor must be the neighbor
                # of at least one of the new
                if pc.is_adjacent(sol[i], sol[k]):
                    adj[i, k] = 1
                    adj[k, i] = 1
                elif remove_trans and (trans_length == 1):
                    # Actively remove transitions between non-neighbors
                    transitions[i, k] = 0
                    transitions[k, i] = 0
                
                for r in new_idx:
                    if pc.is_adjacent(sol[r], sol[k]):
                        adj[r, k] = 1
                        adj[k, r] = 1
                    elif remove_trans and (trans_length == 1):
                        # Actively remove transitions between non-neighbors
                        transitions[r, k] = 0
                        transitions[k, r] = 0
            
            # Update IJ matrix
            IJ = np.pad(IJ, (0,num_new), 'constant')
            adj_k = reachable_within(trans_length, adj, adj)
            sym_adj_change(IJ, adj_k, transitions, i)
            
            for r in new_idx:
                sym_adj_change(IJ, adj_k, transitions, r)
            
            msg += '\n\n Updated adj: \n' + str(adj)
            msg += '\n\n Updated trans: \n' + str(transitions)
            msg += '\n\n Updated IJ: \n' + str(IJ)
        elif vol2 < abs_tol:
            msg += 'Transition found'
            transitions[j,i] = 1
        else:
            msg += 'No transition found, diff vol: ' + str(vol2)
            msg += ', intersect vol: ' + str(vol1)
            transitions[j,i] = 0
        
        logger.info(msg)
        
        iter_count += 1
        
        # no plotting ?
        if not plotit:
            continue
        if plot_partition is None:
            continue
        if iter_count % plot_every != 0:
            continue
        
        tmp_part = PropPreservingPartition(
            domain=part.domain,
            regions=sol, adj=sp.lil_matrix(adj),
            prop_regions=part.prop_regions
        )
        
        # plot pair under reachability check
        ax2.clear()
        si.plot(ax=ax2, color='green')
        sj.plot(ax2, color='red', hatch='o', alpha=0.5)
        plot_transition_arrow(si, sj, ax2)
        
        S0.plot(ax2, color='none', hatch='/', alpha=0.3)
        fig.canvas.draw()
        
        # plot partition
        ax1.clear()
        plot_partition(tmp_part, transitions, ax=ax1, color_seed=23)
        
        # plot dynamics
        ssys.plot(ax1, show_domain=False)
        
        # plot hatched continuous propositions
        part.plot_props(ax1)
        
        fig.canvas.draw()
        
        # scale view based on domain,
        # not only the current polytopes si, sj
        l,u = pc.bounding_box(part.domain)
        ax2.set_xlim(l[0,0], u[0,0])
        ax2.set_ylim(l[1,0], u[1,0])
        
        if save_img:
            fname = 'movie' +str(iter_count).zfill(3)
            fname += '.' + file_extension
            fig.savefig(fname, dpi=250)
        plt.pause(1)

    new_part = PropPreservingPartition(
        domain=part.domain,
        regions=sol, adj=sp.lil_matrix(adj),
        prop_regions=part.prop_regions
    )
    
    # Generate transition system and add transitions       
    ofts = trs.OpenFTS()
    
    adj = sp.lil_matrix(transitions)
    n = adj.shape[0]
    ofts_states = range(n)
    ofts_states = trs.prepend_with(ofts_states, 's')
    
    # add set to destroy ordering
    ofts.states.add_from(set(ofts_states) )
    
    ofts.transitions.add_adj(adj, ofts_states)
    
    # Decorate TS with state labels
    atomic_propositions = set(part.prop_regions)
    ofts.atomic_propositions.add_from(atomic_propositions)
    prop_list = []
    for region in sol:
        state_prop = region.props.copy()
        
        prop_list.append(state_prop)
    
    ofts.states.labels(ofts_states, prop_list)
    
    param = {
        'N':N,
        'trans_length':trans_length,
        'closed_loop':closed_loop,
        'conservative':conservative,
        'use_all_horizon':use_all_horizon,
        'min_cell_volume':min_cell_volume,
        'max_num_poly':max_num_poly
    }
    
    assert(len(prop_list) == n)
    
    return AbstractPwa(
        ppp=new_part,
        ts=ofts,
        ppp2ts=ofts_states,
        pwa=ssys,
        pwa_ppp=part,
        ppp2pwa=orig,
        ppp2sys=subsys_list,
        orig_ppp=orig_ppp,
        ppp2orig=ppp2orig,
        disc_params=param
    )

def reachable_within(trans_length, adj_k, adj):
    """Find cells reachable within trans_length hops.
    """
    if trans_length <= 1:
        return adj_k
    
    k = 1
    while k < trans_length:
        adj_k = np.dot(adj_k, adj)
        k += 1
    adj_k = (adj_k > 0).astype(int)
    
    return adj_k

def sym_adj_change(IJ, adj_k, transitions, i):
    horizontal = adj_k[i, :] -transitions[i, :] > 0
    vertical = adj_k[:, i] -transitions[:, i] > 0
    
    IJ[i, :] = horizontal.astype(int)
    IJ[:, i] = vertical.astype(int)

# DEFUNCT until further notice
def discretize_overlap(closed_loop=False, conservative=False):
    """default False."""
#         
#         if rdiff < abs_tol:
#             logger.info("Transition found")
#             transitions[i,j] = 1
#         
#         elif (vol1 > min_cell_volume) & (risect > rd) & \
#                 (num_new_reg[i] <= num_orig_neigh[i]+1):
#         
#             # Make sure new cell is Region and add proposition lists
#             if len(isect) == 0:
#                 isect = pc.Region([isect], si.props)
#             else:
#                 isect.props = si.props.copy()
#         
#             # Add new state
#             sol.append(isect)
#             size = len(sol)
#             
#             # Add transitions
#             transitions = np.hstack([transitions, np.zeros([size - 1, 1],
#                                     dtype=int) ])
#             transitions = np.vstack([transitions, np.zeros([1, size],
#                                     dtype=int) ])
#             
#             # All sets reachable from orig cell are reachable from both cells
#             transitions[size-1,:] = transitions[i,:]
#             transitions[size-1,j] = 1   # j is reachable from new cell            
#             
#             # Take care of adjacency
#             old_adj = np.nonzero(adj[i,:])[0]
#             
#             adj = np.hstack([adj, np.zeros([size - 1, 1], dtype=int) ])
#             adj = np.vstack([adj, np.zeros([1, size], dtype=int) ])
#             adj[i,size-1] = 1
#             adj[size-1,i] = 1
#             adj[size-1,size-1] = 1
#                                     
#             for k in np.setdiff1d(old_adj,[i,size-1]):
#                 if pc.is_adjacent(sol[size-1],sol[k],overlap=True):
#                     adj[size-1,k] = 1
#                     adj[k, size-1] = 1
#                 else:
#                     # Actively remove (valid) transitions between non-neighbors
#                     transitions[size-1,k] = 0
#                     transitions[k,size-1] = 0
#                     
#             # Assign original proposition cell to new state and update counts
#             if not conservative:
#                 orig = np.hstack([orig, orig[i]])
#             print(num_new_reg)
#             num_new_reg = np.hstack([num_new_reg, 0])
#             num_orig_neigh = np.hstack([num_orig_neigh, np.sum(adj[size-1,:])-1])
#             
#             logger.info("\n Adding state " + str(size-1) + "\n")
#             
#             # Just add adjacent cells for checking,
#             # unless transition already found            
#             IJ = np.hstack([IJ, np.zeros([size - 1, 1], dtype=int) ])
#             IJ = np.vstack([IJ, np.zeros([1, size], dtype=int) ])
#             horiz2 = adj[size-1,:] - transitions[size-1,:] > 0
#             verti2 = adj[:,size-1] - transitions[:,size-1] > 0
#             IJ[size-1,:] = horiz2.astype(int)
#             IJ[:,size-1] = verti2.astype(int)
#         else:
#             logger.info("No transition found, intersect vol: " + str(vol1) )
#             transitions[i,j] = 0
#                   
#     new_part = PropPreservingPartition(
#                    domain=part.domain,
#                    regions=sol, adj=np.array([]),
#                    trans=transitions, prop_regions=part.prop_regions,
#                    original_regions=orig_list, orig=orig)                           
#     return new_part

def discretize_switched(ppp, hybrid_sys, disc_params=None, plot=False):
    """Abstract switched dynamics over given partition.
    
    @type ppp: L{PropPreservingPartition}
    
    @param hybrid_sys: dynamics of switching modes
    @type hybrid_sys: L{HybridSysDyn}
    
    @param disc_params: discretization parameters
        passed to L{discretize},
        see that for details
    @type disc_params: dict (keyed by mode) of dicts
    
    @param plot: save partition images
    @type plot: bool
    
    @return: abstracted dynamics,
        some attributes are dict keyed by mode
    @rtype: L{AbstractSwitched}
    """
    if disc_params is None:
        disc_params = {'N':1, 'trans_length':1}
    
    logger.info('discretizing hybrid system')
    
    modes = hybrid_sys.modes
    mode_nums = hybrid_sys.disc_domain_size
    
    # discretize each abstraction separately
    abstractions = dict()
    for mode in modes:
        logger.debug(30*'-'+'\n')
        logger.info('Abstracting mode: ' + str(mode))
        
        cont_dyn = hybrid_sys.dynamics[mode]
        
        absys = discretize(
            ppp, cont_dyn,
            **disc_params[mode]
        )
        logger.debug('Mode Abstraction:\n' + str(absys) +'\n')
        
        abstractions[mode] = absys
    
    # merge their domains
    (merged_abstr, ap_labeling) = merge_partitions(abstractions)
    n = len(merged_abstr.ppp)
    logger.info('Merged partition has: ' + str(n) + ', states')
    
    # find feasible transitions over merged partition
    trans = dict()
    for mode in modes:
        cont_dyn = hybrid_sys.dynamics[mode]
        
        params = disc_params[mode]
        
        trans[mode] = get_transitions(
            merged_abstr, mode, cont_dyn,
            N=params['N'], trans_length=params['trans_length']
        )
    
    # merge the abstractions, creating a common TS
    merge_abstractions(merged_abstr, trans,
                       abstractions, modes, mode_nums)
    
    if plot:
        plot_mode_partitions(abstractions, merged_abstr)
    
    return merged_abstr

def plot_mode_partitions(abstractions, merged_abs):
    """Save each mode's partition and final merged partition.
    """
    try:
        from tulip.graphics import newax
    except:
        warnings.warn('could not import newax, no partitions plotted.')
        return
    
    ax, fig = newax()
    
    for mode, ab in abstractions.iteritems():
        ab.ppp.plot(plot_numbers=False, ax=ax, trans=ab.ppp.adj)
        plot_annot(ax)
        fname = 'part_' + str(mode) + '.pdf'
        fig.savefig(fname)
    
    merged_abs.ppp.plot(plot_numbers=False, ax=ax, trans=merged_abs.ppp.adj)
    plot_annot
    fname = 'part_merged' + '.pdf'
    fig.savefig(fname)

def plot_annot(ax):
    fontsize = 5
    
    for tick in ax.xaxis.get_major_ticks():
        tick.label1.set_fontsize(fontsize)
    for tick in ax.yaxis.get_major_ticks():
        tick.label1.set_fontsize(fontsize)
    ax.set_xlabel('$v_1$', fontsize=fontsize+6)
    ax.set_ylabel('$v_2$', fontsize=fontsize+6)

def merge_abstractions(merged_abstr, trans, abstr, modes, mode_nums):
    """Construct merged transitions.
    
    @type merged_abstr: L{AbstractSwitched}
    @type abstr: dict of L{AbstractPwa}
    """
    # TODO: check equality of atomic proposition sets
    aps = abstr[modes[0]].ts.atomic_propositions
    
    logger.info('APs: ' + str(aps))
    
    sys_ts = trs.OpenFTS()
    
    # create stats
    n = len(merged_abstr.ppp)
    states = ['s'+str(i) for i in xrange(n) ]
    sys_ts.states.add_from(states)
    
    sys_ts.atomic_propositions.add_from(aps)
    
    # copy AP labels from regions to discrete states
    ppp2ts = states
    for (i, state) in enumerate(ppp2ts):
        props =  merged_abstr.ppp[i].props
        sys_ts.states.label(state, props)
    
    # create mode actions
    sys_actions = [str(s) for e,s in modes]
    env_actions = [str(e) for e,s in modes]
    
    # no env actions ?
    if mode_nums[0] == 0:
        actions_per_mode = {
            (e,s):{'sys_actions':str(s)}
            for e,s in modes
        }
        sys_ts.sys_actions.add_from(sys_actions)
    elif mode_nums[1] == 0:
        # no sys actions
        actions_per_mode = {
            (e,s):{'env_actions':str(e)}
            for e,s in modes
        }
        sys_ts.env_actions.add_from(env_actions)
    else:
        actions_per_mode = {
            (e,s):{'env_actions':str(e), 'sys_actions':str(s)}
            for e,s in modes
        }
        sys_ts.env_actions.add_from([str(e) for e,s in modes])
        sys_ts.sys_actions.add_from([str(s) for e,s in modes])
    
    for mode in modes:
        env_sys_actions = actions_per_mode[mode]
        adj = trans[mode]
        
        sys_ts.transitions.add_labeled_adj(
            adj = adj,
            adj2states = states,
            labels = env_sys_actions
        )
    
    merged_abstr.ts = sys_ts
    merged_abstr.ppp2ts = ppp2ts

def get_transitions(
    abstract_sys, mode, ssys, N=10,
    closed_loop=True,
    trans_length=1
):
    """Find which transitions are feasible in given mode.
    
    Used for the candidate transitions of the merged partition.
    
    @rtype: scipy.sparse.lil_matrix
    """
    logger.info('checking which transitions remain feasible after merging')
    part = abstract_sys.ppp
    
    # Initialize matrix for pairs to check
    IJ = part.adj.copy()
    if trans_length > 1:
        k = 1
        while k < trans_length:
            IJ = np.dot(IJ, part.adj)
            k += 1
        IJ = (IJ > 0).astype(int)
    
    # Initialize output
    n = len(part)
    transitions = sp.lil_matrix((n, n), dtype=int)
    
    # Do the abstraction
    n_checked = 0
    n_found = 0
    while np.sum(IJ) > 0:
        n_checked += 1
        
        ind = np.nonzero(IJ)
        i = ind[1][0]
        j = ind[0][0]
        IJ[j,i] = 0
        
        logger.debug('checking transition: ' + str(i) + ' -> ' + str(j))
        
        si = part[i]
        sj = part[j]
        
        # Use original cell as trans_set
        trans_set = abstract_sys.ppp2pwa(mode, i)[1]
        active_subsystem = abstract_sys.ppp2sys(mode, i)[1]
        
        trans_feasible = is_feasible(
            si, sj, active_subsystem, N,
            closed_loop = closed_loop,
            trans_set = trans_set
        )
                    
        if trans_feasible:
            transitions[j,i] = 1 
            msg = '\t Feasible transition.'
            n_found += 1
        else:
            transitions[j,i] = 0
            msg = '\t Not feasible transition.'
        logger.debug(msg)
    logger.info('Checked: ' + str(n_checked))
    logger.info('Found: ' + str(n_found))
    logger.info('Survived merging: ' + str(float(n_found) / n_checked) + ' % ')
            
    return transitions
    
def merge_partitions(abstractions):
    """Merge multiple abstractions.
    
    @param abstractions: keyed by mode
    @type abstractions: dict of L{AbstractPwa}
    
    @return: (merged_abstraction, ap_labeling)
        where:
            - merged_abstraction: L{AbstractSwitched}
            - ap_labeling: dict
    """
    if len(abstractions) == 0:
        warnings.warn('Abstractions empty, nothing to merge.')
        return
    
    # consistency check
    for ab1 in abstractions.itervalues():
        for ab2 in abstractions.itervalues():
            p1 = ab1.ppp
            p2 = ab2.ppp
            
            if p1.prop_regions != p2.prop_regions:
                msg = 'merge: partitions have different sets '
                msg += 'of continuous propositions'
                raise Exception(msg)
            
            if not (p1.domain.A == p2.domain.A).all() or \
            not (p1.domain.b == p2.domain.b).all():
                raise Exception('merge: partitions have different domains')
            
            # check equality of original PPP partitions
            if ab1.orig_ppp == ab2.orig_ppp:
                logger.info('original partitions happen to be equal')
    
    init_mode = abstractions.keys()[0]
    all_modes = set(abstractions)
    remaining_modes = all_modes.difference(set([init_mode]))
    
    print('init mode: ' + str(init_mode))
    print('all modes: ' + str(all_modes))
    print('remaining modes: ' + str(remaining_modes))
    
    # initialize iteration data
    prev_modes = [init_mode]
    
    ab0 = abstractions[init_mode]
    regions = list(ab0.ppp)
    parents = {init_mode:range(len(regions) )}
    ap_labeling = {i:reg.props for i,reg in enumerate(regions)}
    
    for cur_mode in remaining_modes:
        ab2 = abstractions[cur_mode]
        
        r = merge_partition_pair(
            regions, ab2, cur_mode, prev_modes,
            parents, ap_labeling
        )
        regions, parents, ap_labeling = r
        
        prev_modes += [cur_mode]
    
    new_list = regions
    
    # build adjacency based on spatial adjacencies of
    # component abstractions.
    # which justifies the assumed symmetry of part1.adj, part2.adj
    n_reg = len(new_list)
    
    adj = np.zeros([n_reg, n_reg], dtype=int)
    for i, reg_i in enumerate(new_list):
        for j, reg_j in enumerate(new_list[0:i]):
            touching = False
            for mode in abstractions:
                pi = parents[mode][i]
                pj = parents[mode][j]
                
                part = abstractions[mode].ppp
                
                if (part.adj[pi, pj] == 1) or (pi == pj):
                    touching = True
                    break
            
            if not touching:
                continue
            
            if pc.is_adjacent(reg_i, reg_j):
                adj[i,j] = 1
                adj[j,i] = 1
        adj[i,i] = 1
    
    ppp = PropPreservingPartition(
        domain=ab0.ppp.domain,
        regions=new_list,
        prop_regions=ab0.ppp.prop_regions,
        adj=adj
    )
    
    abstraction = AbstractSwitched(
        ppp=ppp,
        modes=abstractions,
        ppp2modes=parents,
    )
    
    return (abstraction, ap_labeling)

def merge_partition_pair(
    old_regions, ab2,
    cur_mode, prev_modes,
    old_parents, old_ap_labeling
):
    """Merge an Abstraction with the current partition iterate.
    """
    logger.info('merging partitions')
    
    part2 = ab2.ppp
    
    modes = prev_modes + [cur_mode]
    
    new_list = []
    parents = {mode:dict() for mode in modes}
    ap_labeling = dict()
    
    for i in xrange(len(old_regions)):
        for j in xrange(len(part2)):
            isect = pc.intersect(old_regions[i],
                                 part2[j])
            rc, xc = pc.cheby_ball(isect)
            
            # no intersection ?
            if rc < 1e-5:
                continue
            logger.info('merging region: A' + str(i) +
                        ', with: B' + str(j))
            
            # if Polytope, make it Region
            if len(isect) == 0:
                isect = pc.Region([isect])
            
            # label the Region with propositions
            isect.props = old_regions[i].props.copy()
            
            new_list.append(isect)
            idx = new_list.index(isect)
            
            # keep track of parents
            for mode in prev_modes:
                parents[mode][idx] = old_parents[mode][i]
            parents[cur_mode][idx] = j
            
            # union of AP labels from parent states
            ap_label_1 = old_ap_labeling[i]
            ap_label_2 = ab2.ts.states.label_of('s'+str(j))['ap']
            
            logger.debug('AP label 1: ' + str(ap_label_1))
            logger.debug('AP label 2: ' + str(ap_label_2))
            
            # original partitions may be different if pwa_partition used
            # but must originate from same initial partition,
            # i.e., have same continuous propositions, checked above
            #
            # so no two intersecting regions can have different AP labels,
            # checked here
            if ap_label_1 != ap_label_2:
                msg = 'Inconsistent AP labels between intersecting regions\n'
                msg += 'of partitions of switched system.'
                raise Exception(msg)
            
            ap_labeling[idx] = ap_label_1
    
    return new_list, parents, ap_labeling

def _all_dict(r, names='?'):
    """Return True if all elements in r are dict.
    
    False if all elements are not dict.
    Otherwise raise Exception mentioning C{names}.
    """
    f = lambda x: isinstance(x, dict)
    
    n_dict = len(filter(f, r))
    
    if n_dict == 0:
        return False
    elif n_dict == len(r):
        return True
    else:
        msg = 'Mixed dicts with non-dicts among: ' + str(names)
        raise Exception(msg)
