# Copyright 2014-2018 The PySCF Developers. All Rights Reserved.
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
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

import sys
import os
import pyscf.lib.logger

try:
    from mpi4py import MPI
    MPI_comm = MPI.COMM_WORLD
    MPI_rank = MPI_comm.Get_rank()
except (ImportError, ModuleNotFoundError):
    MPI = False

#base_output = "pyscf.log"
#default_output = base_output
#idx = 0
#while os.path.isfile(default_output):
#    idx += 1
#    default_output = base_output + ".%d" % idx
## Make sure all MPI ranks agree on the latest logfile
#MPI_comm.Barrier()

if sys.version_info >= (2,7):

    import argparse

    def cmd_args():
        '''
        get input from cmdline
        '''
        parser = argparse.ArgumentParser(allow_abbrev=False)
        parser.add_argument('-v', '--verbose',
                            action='store_false', dest='verbose', default=0,
                            help='make lots of noise')
        parser.add_argument('-q', '--quiet',
                            action='store_false', dest='quite', default=False,
                            help='be very quiet')
        parser.add_argument('-o', '--output',
                            dest='output', metavar='FILE', help='write output to FILE')#,
                            #default=default_output)
                            #default="pyscf.log")
        parser.add_argument('-m', '--max-memory',
                            action='store', dest='max_memory', metavar='NUM',
                            help='maximum memory to use (in MB)')

        (opts, args_left) = parser.parse_known_args()

        # Append MPI rank to output file
        if MPI and opts.output is not None and MPI_rank > 0:
            logname, ext = opts.output.rsplit(".", 1)
            opts.output = logname + (".mpi%d" % MPI_rank)
            if ext:
                opts.output += (".%s" % ext)

        if opts.quite:
            opts.verbose = pyscf.lib.logger.QUIET

        if opts.verbose:
            opts.verbose = pyscf.lib.logger.DEBUG

        if opts.max_memory:
            opts.max_memory = float(opts.max_memory)

        return opts

else:
    import optparse

    def cmd_args():
        '''
        get input from cmdline
        '''
        parser = optparse.OptionParser()
        parser.add_option('-v', '--verbose',
                          action='store_false', dest='verbose',
                          help='make lots of noise')
        parser.add_option('-q', '--quiet',
                          action='store_false', dest='quite', default=False,
                          help='be very quiet')
        parser.add_option('-o', '--output',
                          dest='output', metavar='FILE', help='write output to FILE')
        parser.add_option('-m', '--max-memory',
                          action='store', dest='max_memory', metavar='NUM',
                          help='maximum memory to use (in MB)')

        (opts, args_left) = parser.parse_args()

        if opts.quite:
            opts.verbose = pyscf.lib.logger.QUIET

        if opts.verbose:
            opts.verbose = pyscf.lib.logger.DEBUG

        if opts.max_memory:
            opts.max_memory = float(opts.max_memory)

        return opts


if __name__ == '__main__':
    opts = cmd_args()
    print(opts.verbose, opts.output, opts.max_memory)
