#!/usr/bin/env python
import logging
from setuptools import setup
import os
# inline:
# import git


NAME = 'tulip'
VERSION_FILE = '{name}/_version.py'.format(name=NAME)
MAJOR = 1
MINOR = 2
MICRO = 1
VERSION = '{major}.{minor}.{micro}'.format(
    major=MAJOR, minor=MINOR, micro=MICRO)
VERSION_TEXT = (
    '# This file was generated from setup.py\n'
    "version = '{version}'\n")
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
classifiers = [
    'Development Status :: 2 - Pre-Alpha',
    'Intended Audience :: Science/Research',
    'License :: OSI Approved :: BSD License',
    'Operating System :: OS Independent',
    'Programming Language :: Python',
    'Programming Language :: Python :: 2',
    'Programming Language :: Python :: 2.7',
    'Topic :: Scientific/Engineering']
package_data = {
    'tulip': ['commit_hash.txt'],
    'tulip.transys.export': ['d3.v3.min.js'],
    'tulip.spec': ['parsetab.py']}


def package_jtlv():
    if os.path.exists(os.path.join('tulip', 'interfaces', 'jtlv_grgame.jar')):
        print('Found optional JTLV-based solver.')
        package_data['tulip.interfaces'] = ['jtlv_grgame.jar']
    else:
        print('The jtlv synthesis tool was not found. '
              'Try extern/get-jtlv.sh to get it.\n'
              'It is an optional alternative to gr1c, '
              'the default GR(1) solver of TuLiP.')


def git_version(version):
    """Return version with local version identifier."""
    import git
    repo = git.Repo('.git')
    repo.git.status()
    sha = repo.head.commit.hexsha
    if repo.is_dirty():
        return '{v}.dev0+{sha}.dirty'.format(
            v=version, sha=sha)
    # commit is clean
    # is it release of `version` ?
    try:
        tag = repo.git.describe(
            match='v[0-9]*', exact_match=True,
            tags=True, dirty=True)
    except git.GitCommandError:
        return '{v}.dev0+{sha}'.format(
            v=version, sha=sha)
    assert tag[1:] == version, (tag, version)
    return version


def run_setup():
    # Build PLY table, to be installed as tulip package data
    try:
        import tulip.spec.lexyacc
        tabmodule = tulip.spec.lexyacc.TABMODULE.split('.')[-1]
        outputdir = 'tulip/spec'
        parser = tulip.spec.lexyacc.Parser()
        parser.build(tabmodule, outputdir=outputdir,
                     write_tables=True,
                     debug=True, debuglog=logger)
        plytable_build_failed = False
    except Exception as e:
        print('Failed to build PLY tables: {e}'.format(e=e))
        plytable_build_failed = True
    # version
    try:
        version = git_version(VERSION)
    except AssertionError:
        raise
    except:
        print('No git info: Assume release.')
        version = VERSION
    s = VERSION_TEXT.format(version=version)
    with open(VERSION_FILE, 'w') as f:
        f.write(s)
    # setup
    package_jtlv()
    setup(
        name=NAME,
        version=version,
        description='Temporal Logic Planning (TuLiP) Toolbox',
        author='Caltech Control and Dynamical Systems',
        author_email='tulip@tulip-control.org',
        url='http://tulip-control.org',
        bugtrack_url='http://github.com/tulip-control/tulip-control/issues',
        license='BSD',
        classifiers=classifiers,
        install_requires=[
            'ply >= 3.4',
            'networkx >= 1.6',
            'numpy >= 1.7',
            'pydot >= 1.0.28',
            'scipy'],
        extras_require={
            'hybrid': ['cvxopt >= 1.1.7',
                       'polytope >= 0.1.1']},
        tests_require=[
            'nose',
            'matplotlib'],
        packages=[
            'tulip', 'tulip.transys', 'tulip.transys.export',
            'tulip.abstract', 'tulip.spec',
            'tulip.interfaces'],
        package_dir={'tulip': 'tulip'},
        package_data=package_data)
    # ply failed ?
    if plytable_build_failed:
        print("!"*65)
        print("    Failed to build PLY table.  Please run setup.py again.")
        print("!"*65)


if __name__ == '__main__':
    run_setup()
