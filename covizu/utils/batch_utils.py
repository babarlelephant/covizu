import subprocess
from Bio import Phylo
from covizu import clustering, treetime, beadplot


def build_timetree(by_lineage, args, callback=None):
    """ Generate time-scaled tree of Pangolin lineages """
    fasta = treetime.retrieve_genomes(by_lineage, ref_file=args.ref)

    if callback:
        callback("Reconstructing tree with {}".format(args.ft2bin))
    nwk = treetime.fasttree(fasta, binpath=args.ft2bin)

    if callback:
        callback("Reconstructing time-scaled tree with {}".format(args.ttbin))
    nexus_file = treetime.treetime(nwk, fasta, outdir=args.outdir, binpath=args.ttbin,
                                   clock=args.clock, verbosity=0)

    # writes output to treetime.nwk at `nexus_file` path
    return treetime.parse_nexus(nexus_file, fasta)


def beadplot_serial(lineage, features, args, callback=None):
    """ Compute distance matrices and reconstruct NJ trees """
    # bootstrap sampling and NJ tree reconstruction, serial mode
    trees, labels = clustering.build_trees(features, args, callback=callback)
    if trees is None:
        # lineage only has one variant, no meaningful tree
        beaddict = {'lineage': lineage, 'nodes': {}, 'edges': []}

        # use earliest sample as variant label
        intermed = [label.split('|')[::-1] for label in labels[0]]
        intermed.sort()
        variant = intermed[0][1]
        beaddict['nodes'].update({variant: []})

        for coldate, accn, label1 in intermed:
            beaddict['nodes'][variant].append([coldate, accn, label1])
        return beaddict

    # generate majority consensus tree
    ctree = clustering.consensus(iter(trees), cutoff=args.boot_cutoff)

    # collapse polytomies and label internal nodes
    label_dict = dict([(str(idx), lst) for idx, lst in enumerate(labels)])
    ctree = beadplot.annotate_tree(ctree, label_dict, callback=callback)

    # convert to JSON format
    beaddict = beadplot.serialize_tree(ctree)
    beaddict.update({'lineage': lineage})
    return beaddict


def import_labels(handle, callback=None):
    """ Load map of genome labels to tip indices from CSV file """
    result = {}
    _ = next(handle)  # skip header line
    for line in handle:
        try:
            qname, idx = line.strip('\n').split(',')
        except ValueError:
            if callback:
                callback("import_labels() failed to parse line {}".format(line), level="ERROR")
            raise  # issue #206, sequence label contains delimiter

        if idx not in result:
            result.update({idx: []})
        result[idx].append(qname)
    return result


def make_beadplots(by_lineage, args, callback=None, t0=None):
    """
    Wrapper for beadplot_serial - divert to clustering.py in MPI mode if
    lineage has too many genomes.

    :param by_lineage:  dict, feature vectors stratified by lineage
    :param args:  Namespace, from argparse.ArgumentParser()
    :param t0:  float, datetime.timestamp.
    :return:  list, beadplot data by lineage
    """
    result = []
    for lineage, features in by_lineage.items():
        if callback:
            callback('start {}, {} entries'.format(lineage, len(features)))

        if len(features) < args.mincount:
            # serial processing
            if len(features) == 0:
                continue  # empty lineage, skip (should never happen)
            beaddict = beadplot_serial(lineage, features, args)
        else:
            # call out to MPI
            cmd = [
                "mpirun", "--machinefile", args.machine_file,
                "python3", "covizu/clustering.py",
                 args.bylineage, lineage,  # positional arguments <JSON file>, <str>
                 "--nboot", str(args.nboot), "--outdir", "data"
            ]
            if t0:
                cmd.extend(["--timestamp", str(t0)])
            subprocess.check_call(cmd)

            # import trees
            outfile = open('data/{}.nwk'.format(lineage))
            trees = Phylo.parse(outfile, 'newick')  # note this returns a generator

            # import label map
            with open('data/{}.labels.csv'.format(lineage)) as handle:
                label_dict = import_labels(handle)

            # generate beadplot data
            ctree = clustering.consensus(trees, cutoff=args.boot_cutoff, callback=callback)
            outfile.close()  # done with Phylo.parse generator

            ctree = beadplot.annotate_tree(ctree, label_dict)
            beaddict = beadplot.serialize_tree(ctree)

        beaddict.update({'lineage': lineage})
        result.append(beaddict)

    return result
