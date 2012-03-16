#!/usr/bin/env python

"""
Given PSRCHIVE archives reduce them so they are ready to
produce TOAs.

Patrick Lazarus, Nov. 22, 2011
"""
import optparse
import datetime
import os.path
import os
import tempfile
import sys
import traceback

import numpy as np
import matplotlib.pyplot as plt
import psrchive

import toas
import diagnose
import utils
import clean
import clean_utils
import combine
import config
import errors

class ReductionLog(object):
    """An object to log reduction of timing data.
    """
    def __init__(self, infns, fn):
        # calculate MD5 checksum of all input file
        self.infile_md5s = {}
        for infn in infns:
            self.infile_md5s[infn.fn] = utils.get_md5sum(infn.fn)
        
        self.fn = fn

        # Find the git hash of the code
        self.githash = utils.get_githash()
        self.dirtyrepo = utils.is_gitrepo_dirty()

        # Report the pwd
        self.workdir = os.getcwd()

        # Report the command line used 
        self.cmdline = " ".join(sys.argv)

    def start(self):
        self.starttime = datetime.datetime.now()

    def finish(self):
        self.endtime = datetime.datetime.now()
        self.time_elapsed = self.endtime - self.starttime

    def failure(self, exctype, excval, exctb):
        # Make sure we don't get colour tags in our log's traceback,
        # they're distracting
        tmp = config.colour
        config.colour = False
        self.epilog = "".join(traceback.format_exception(exctype, excval, exctb))
        config.colour = tmp

    def success(self, outfns, toastrs):
        self.outfns = outfns
        self.toastrs = toastrs

        self.epilog = "Output %d data files:" % len(outfns)
        for outfn in outfns:
            self.epilog += "\n    %s (MD5: %s)" % \
                            (outfn.fn, utils.get_md5sum(outfn.fn))

        self.epilog += "\nGenerated %d TOAs:" % len(toastrs)
        for toastr in toastrs:
            self.epilog += "\n    %s" % toastr

    def to_file(self):
        f = open(self.fn, 'w')
        f.write("Starting data reduction: %s\n" % str(self.starttime))
        f.write("Current Coast Guard git hash: %s" % self.githash)
        if self.dirtyrepo:
            f.write(" (dirty)\n")
        else:
            f.write("\n")
        f.write("Current working directory: %s\n" % self.workdir)
        f.write("Complete command line: %s\n" % self.cmdline)
        f.write("Reduced %d files:\n" % len(self.infile_md5s))
        for key in sorted(self.infile_md5s.keys()):
            f.write("    %s (MD5: %s)\n" % (key, self.infile_md5s[key]))
        f.write("Reduction finished: %s (Time elapsed: %s)\n" % \
                (str(self.endtime), str(self.time_elapsed)))
        f.write(self.epilog+"\n")
        f.close()


class ReductionJob(object):
    """An object to represent the reduction of an observation.
    """
    def __init__(self, infns, outfn):
        """Given a list of PSRCHIVE file names create a
            ReductionJob object.

            Input:
                infns: A list of input PSRCHIVE archive file names.
                outfn: The name of the reduced archive output.

            Output:
                job: The Reduction job object.
        """
        self.infns = infns
        self.outfn = outfn
        self.basenm = os.path.splitext(self.outfn)[0]

        logfn = utils.get_outfn(self.basenm+'.log', infns[0])
        self.log = ReductionLog(infns, logfn)
        
        self.cfg = config.CoastGuardConfigs()
        self.cfg.get_default_configs()
        self.cfg.get_configs_for_archive(self.infns[0])
 
    def run(self):
        """Call method to reduce archives, and take care of logging.

            Inputs:
                None

            Outputs:
                None
        """
        
        self.log.start()
        try:
            cleanfns, toastrs = self.reduce_archives()
        except Exception:
            self.log.failure(*sys.exc_info())
            sys.stderr.write("".join(traceback.format_exception(*sys.exc_info())))
            raise errors.DataReductionFailed("Data reduction failed! " \
                        "Check log file: %s" % (self.log.fn))
        else:
            self.log.success(cleanfns, toastrs)
        finally:
            self.log.finish()
            self.log.to_file()
        return cleanfns, toastrs

    def reduce_archives(self): 
        """Group input files into sub-bands then remove the edges of each 
            sub-band to remove the artifacts caused by aliasing. Finally, 
            combine the sub-bands into a single output file.
 
            The combined sub-band files are not saved.
 
            Inputs:
                None
                
            Outputs:
                outfn: The final reduced file name.
                toas: TOA strings.
        """
        if len(self.infns) > 1:
            combinedfns = combine_all(to_combine, self.basenm+".cmb", \
                            maxspan=cfg.combine_maxspan, \
                            maxgap=cfg.combine_maxgap, \
                            num_to_trim=cfg.nchan_to_trim)
        else:
            combinefns = self.infns
        
        cleanfns = []
        toastrs = []
        for combinefn in combinefns:
            # Create diagnostic plots for pre-cleaned data
            utils.print_info("Creating diagnostics for %s" % combinefn.fn, 1)
            for func_key in self.cfg.funcs_to_plot:
                diagnose.make_diagnostic_figure(combinefn.fn, \
                                            rmbaseline=self.cfg.diagnostic_rmbaseline, \
                                            dedisp=self.cfg.diagnostic_dedisp, \
                                            centre_prof=self.cfg.diagnostic_centre_prof, \
                                            rmprof=True, \
                                            func_key=func_key, \
                                            log=self.cfg.diagnostic_logcolours, \
                                            vmin=self.cfg.diagnostic_vmin, \
                                            vmax=self.cfg.diagnostic_vmax)
                plt.savefig("%s_diag_noprof_%s.png" % (combinefn.fn, func_key), dpi=600)
                diagnose.make_diagnostic_figure(combinefn.fn, \
                                            rmbaseline=self.cfg.diagnostic_rmbaseline, \
                                            dedisp=self.cfg.diagnostic_dedisp, \
                                            centre_prof=self.cfg.diagnostic_centre_prof, \
                                            rmprof=False, \
                                            func_key=func_key, \
                                            log=self.cfg.diagnostic_logcolours, \
                                            vmin=self.cfg.diagnostic_vmin, \
                                            vmax=self.cfg.diagnostic_vmax)
                plt.savefig("%s_diag_%s.png" % (combinefn.fn, func_key), dpi=600)
 
            # Clean the data
            utils.print_info("Cleaning %s" % combinefn.fn, 1)
            ar = psrchive.Archive_load(combinefn.fn)
            outfn = utils.get_outfn(self.outfn, combinefn)
            clean.deep_clean(ar, outfn, self.cfg.clean_chanthresh, \
                                self.cfg.clean_subintthresh, self.cfg.clean_binthresh)
            
            # Re-create diagnostic plots for clean data
            utils.print_info("Creating diagnostics for %s" % outfn, 1)
            for func_key in self.cfg.funcs_to_plot:
                diagnose.make_diagnostic_figure(outfn, \
                                                rmbaseline=self.cfg.diagnostic_rmbaseline, \
                                                dedisp=self.cfg.diagnostic_dedisp, \
                                                centre_prof=self.cfg.diagnostic_centre_prof, \
                                                rmprof=True, \
                                                func_key=func_key, \
                                                log=self.cfg.diagnostic_logcolours, \
                                                vmin=self.cfg.diagnostic_vmin, \
                                                vmax=self.cfg.diagnostic_vmax)
                plt.savefig("%s_diag_noprof_%s.png" % (outfn, func_key), dpi=600)
                diagnose.make_diagnostic_figure(outfn, \
                                                rmbaseline=self.cfg.diagnostic_rmbaseline, \
                                                dedisp=self.cfg.diagnostic_dedisp, \
                                                centre_prof=self.cfg.diagnostic_centre_prof, \
                                                rmprof=False, \
                                                func_key=func_key, \
                                                log=self.cfg.diagnostic_logcolours, \
                                                vmin=self.cfg.diagnostic_vmin, \
                                                vmax=self.cfg.diagnostic_vmax)
                plt.savefig("%s_diag_%s.png" % (outfn, func_key), dpi=600)

            cleanfns.append(utils.ArchiveFile(outfn))
            
            # Make TOAs
            utils.print_info("Generating TOAs", 1)
            stdfn = toas.get_standard(outfn, self.cfg.base_standards_dir)
            utils.print_info("Standard profile: %s" % stdfn, 2)
            toastrs.extend(toas.get_toas(outfn, stdfn, self.cfg.ntoa_time, \
                                        self.cfg.ntoa_freq))
        return cleanfns, toastrs


def main():
    print ""
    print "        reduce.py"
    print "     Patrick  Lazarus"
    print ""
    file_list = args + options.from_glob
    to_exclude = options.excluded_files + options.excluded_by_glob
    to_reduce = utils.exclude_files(file_list, to_exclude)
    print "Number of input files: %d" % len(to_reduce)
    
    to_reduce = [utils.ArchiveFile(fn) for fn in to_reduce]
    
    job = ReductionJob(to_reduce, options.outfn)
    outfns, toastrs = job.run()
    print "Output file names:"
    for outfn in outfns:
        print "    %s" % outfn.fn

    print "TOAs:"
    print "\n".join(toastrs)


if __name__=="__main__":
    parser = utils.DefaultOptions(usage="%prog [OPTIONS] FILES ...", \
                        description="Given a list of PSRCHIVE file names " \
                                    "reduce them so they are ready to " \
                                    "generate TOAs. A single output file " \
                                    "is produced.")
    parser.add_option('-o', '--outname', dest='outfn', type='string', \
                        help="The output (reduced) file's name. " \
                            "(Default: '%(name)s_%(yyyymmdd)s_%(secs)05d_reduced.ar')", \
                        default="%(name)s_%(yyyymmdd)s_%(secs)05d_reduced.ar")
    parser.add_option('-g', '--glob', dest='from_glob', action='callback', \
                        callback=utils.get_files_from_glob, default=[], \
                        type='string', \
                        help="Glob expression of input files. Glob expression " \
                            "should be properly quoted to not be expanded by " \
                            "the shell prematurely. (Default: no glob " \
                            "expression is used.)") 
    parser.add_option('-x', '--exclude-file', dest='excluded_files', \
                        type='string', action='append', default=[], \
                        help="Exclude a single file. Multiple -x/--exclude-file " \
                            "options can be provided. (Default: don't exclude " \
                            "any files.)")
    parser.add_option('--exclude-glob', dest='excluded_by_glob', action='callback', \
                        callback=utils.get_files_from_glob, default=[], \
                        type='string', \
                        help="Glob expression of files to exclude as input. Glob " \
                            "expression should be properly quoted to not be " \
                            "expanded by the shell prematurely. (Default: " \
                            "exclude any files.)")
    options, args = parser.parse_args()
    main()
