"""
This module is used to label cluster points with TrueLabel and PredictedLabel.
TrueLabel is obtained from manual gating.
PredictedLabel is obtained by comparing the centroid of each cluster with centroid of each manual gating.
The label is obtained from the closest gate.

The module reads in a JSON config file. The location of this config file must be specified as the first argument
when running the script.

Givanna Putri, July 2018.
"""

import pandas as pd
import numpy as np
import argparse
import multiprocessing as mp
import json
import os
import textwrap

from shutil import copyfile

from collections import defaultdict

parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=textwrap.dedent('''\
                                 Cluster points labeller
                                 -----------------------
                                 This script label cluster points generated by chronoclust.
                                 It requires a config file formatted as JSON.
                                 
                                 The config file must contain all the elements listed below:
                                 {
                                    "DIMENSIONALITY_OF_CLUSTER_POINTS": 3,
                                    "CLUSTER_RESULT_FILE": "/work_dir/result.csv",
                                    "DATA_PER_TIMEPOINT": [
                                        {
                                            "TIMEPOINT": 0,
                                            "CLUSTER_POINTS_FILE": "/work_dir/cluster_points_D0.csv",
                                            "EXPERT_LABELS_FILE": "/dataset/d0.csv"
                                        },
                                        {
                                            "TIMEPOINT": 1,
                                            "CLUSTER_POINTS_FILE": "/work_dir/cluster_points_D1.csv",
                                            "EXPERT_LABELS_FILE": "/dataset/d1.csv"
                                        },
                                        {
                                            "TIMEPOINT": 2,
                                            "CLUSTER_POINTS_FILE": "/work_dir/cluster_points_D2.csv",
                                            "EXPERT_LABELS_FILE": "/dataset/d2.csv"
                                        },
                                        {
                                            "TIMEPOINT": 3,
                                            "CLUSTER_POINTS_FILE": "/work_dir/cluster_points_D3.csv",
                                            "EXPERT_LABELS_FILE": "/dataset/d3.csv"
                                        },
                                        {
                                            "TIMEPOINT": 4,
                                            "CLUSTER_POINTS_FILE": "/work_dir/cluster_points_D4.csv",
                                            "EXPERT_LABELS_FILE": "/dataset/d4.csv"
                                        }
                                    ],
                                    "EXPERT_LABELS_CENTROID_FILE": "/dataset/centroids.csv",
                                    "BACKUP_DIR": "/work_dir"
                                    }
                                 } 
                                 DIMENSIONALITY_OF_CLUSTER_POINTS: number of dimensions in the cluster points.
                                 CLUSTER_RESULT_FILE: the result file generated by chronoclust. It contains details of each cluster.
                                 DATA_PER_TIMEPOINT: data_autoencoder per each time point.
                                    TIMEPOINT: the time point.
                                    CLUSTER_POINTS_FILE: cluster points (generated by chronoclust) for this time point.
                                    EXPERT_LABELS_FILE: expert label file for this time point
                                 BACKUP_DIR: directory to backup the cluster points. Don't want to lose the original.
                                 
                                 IMPORTANT! Please make sure that the TIMEPOINT in DATA_PER_TIMEPOINT correspond to 
                                 time_point column in CLUSTER_RESULT_FILE.
                                 ''')
                                 )
parser.add_argument('config', nargs='?', help='Location of the config file (in JSON format) for this script.')
args = parser.parse_args()

# These MUST to be assigned from config json file.
data_per_timepoint = {}
num_dimensions = 0
cluster_result_file = None
expert_label_centroid_file = None

# Expert labels WILL be set using get_cluster_label_mapping_per_day().
predicted_labels_mapping = None

# WILL be set using create_backup_dir
backup_dir = None


def parse_config_file():
    """
    Before running just about any function here, you will need to parse the config file first.
    So run this before running any functions!
    This is because it sets global variables required to do any labelling.
    """

    # Need this as it defines the global variable
    global num_dimensions, cluster_result_file, data_per_timepoint, expert_label_centroid_file, backup_dir

    # Parse json config file
    with open(args.config, 'r') as f:
        config = json.load(f)

        num_dimensions = int(config['DIMENSIONALITY_OF_CLUSTER_POINTS'])
        cluster_result_file = config['CLUSTER_RESULT_FILE']

        expert_label_centroid_file = config['EXPERT_LABELS_CENTROID_FILE']

        for d in config['DATA_PER_TIMEPOINT']:
            # tuple where left is the cluster points and right is the expert label file
            data = (d['CLUSTER_POINTS_FILE'], d['EXPERT_LABELS_FILE'])
            data_per_timepoint[d['TIMEPOINT']] = data

        backup_dir = config['BACKUP_DIR']

        # Create a backup directory for the original files of the labelling result so we don't lose the original
        # in the event of failure to update.
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)


def get_cluster_label_mapping_per_day():
    """
    This method group the 'mapped' expert labels for each cluster in each day into a 2d dictionary.
    Mapped because this is the approximate expert label given the centroid of each cluster.
    The dictionary looks like: {
        0: {A: Monoblasts, B: Eosinophils},
        1: {A: Monoblasts, A|1: Monocytes, B: Eosinophils}
    }
    'Monoblasts, Monocytes, Eosinophils' are the predicted label present in the file under 'predicted_label' column.
    'A, B, A|1' are the cluster id present in the file under 'tracking_by_lineage' column.
    '0, 1' are the time points present in the file under 'time_point' column.
    """

    # We're setting the global predicted labels
    global predicted_labels_mapping

    df = pd.read_csv(cluster_result_file)

    predicted_labels_mapping = defaultdict(dict)
    for idx, row in df.iterrows():
        cluster_id = row['tracking_by_lineage']
        try:
            day = int(row['time_point'])
        except ValueError:
            continue
        predicted_labels_mapping[day][cluster_id] = row['predicted_label']


def process_each_day_old(timepoint):

    data = data_per_timepoint[timepoint]
    cluster_points_file = data[0]
    cluster_points_df = pd.read_csv(cluster_points_file)

    dataset_attributes_cluster_file = list(cluster_points_df)[2:num_dimensions+2]

    true_label_file_df = pd.read_csv(data[1])
    true_label_dict = {}
    for idx, row in true_label_file_df.iterrows():
        attr_vals = tuple([round(row[a], 1) for a in dataset_attributes_cluster_file])
        true_label_dict[attr_vals] = row['PopName']

    predicted_label_for_current_day = None
    if predicted_labels_mapping is not None:
        predicted_label_for_current_day = predicted_labels_mapping.get(timepoint)

    # Get the true and predicted label from expert labels
    true_labels = []
    predicted_labels = []
    for idx, row in cluster_points_df.iterrows():
        attr_vals = tuple([round(row[a], 1) for a in dataset_attributes_cluster_file])

        # Determine the true label
        label = true_label_dict.get(attr_vals, 'Noise')
        true_labels.append(label)

        # If there are no clusters in the day, predicted_labels will be None. We need to cater for this.
        # We need to pre-assign predicted_label as Noise as it will not get assigned Noise if there are no clusters
        # in the result file.
        predicted_label = 'Noise'
        if predicted_label_for_current_day is not None:
            predicted_label = predicted_label_for_current_day.get(row['cluster_id'], 'Noise')
        predicted_labels.append(predicted_label)

    # Backup result
    cluster_points_filename = cluster_points_file.split('/')[-1]
    cluster_points_df.to_csv('{}/{}'.format(backup_dir, cluster_points_filename), index=False)

    # Assign the labels and write the result out
    cluster_points_df = cluster_points_df.assign(TrueLabel=pd.Series(np.array(true_labels)).values)
    cluster_points_df = cluster_points_df.assign(PredictedLabel=pd.Series(np.array(predicted_labels)).values)
    cluster_points_df.to_csv(cluster_points_file, index=False)


def process_each_day(timepoint):
    """
    Newer much faster method.
    :param timepoint:
    :return:
    """
    data = data_per_timepoint[timepoint]

    cluster_points_file = data[0]
    cluster_points_filename = cluster_points_file.split('/')[-1]
    cluster_points_df = pd.read_csv(cluster_points_file)
    cluster_points_df = cluster_points_df.round(1)
    true_label_df = pd.read_csv(data[1])

    dataset_attributes = list(cluster_points_df)[2: num_dimensions + 2]

    # Get predicted label from result file
    predicted_label_for_current_day = None
    if predicted_labels_mapping is not None:
        predicted_label_for_current_day = predicted_labels_mapping.get(timepoint)

    # This is where we backup the duplicates!
    backup_duplicated_points(cluster_points_df, cluster_points_filename, dataset_attributes,
                             predicted_label_for_current_day, true_label_df)

    # Merge the cluster points and the true label csv file. Merging based on the dataset attribute values only.
    merge_df = cluster_points_df.merge(true_label_df, on=dataset_attributes, how='left')

    # Then we drop the duplicates
    merge_df.drop_duplicates(inplace=True, subset=dataset_attributes, keep=False)

    # Just cosmetic renaming. Rather than pop name, we call them the true label.
    merge_df.rename(columns={'PopName': 'TrueLabel'}, inplace=True)

    # Map the cluster id and the predicted label found when clustering.
    merge_df['PredictedLabel'] = merge_df['cluster_id'].map(predicted_label_for_current_day)

    # Well if there are columns that are empty, it means the true or predicted label doesn't exists. They are noise.
    merge_df.fillna("Noise", inplace=True)

    # Round values to 5 dp. Stupid pandas mess it up
    tmp = merge_df.select_dtypes(include=[np.number])
    merge_df.loc[:, tmp.columns] = np.round(tmp, 5)

    # Backup result
    copyfile(cluster_points_file, '{}/{}'.format(backup_dir, cluster_points_filename))

    # Assign the labels and write the result out
    merge_df.to_csv(cluster_points_file, index=False)


def backup_duplicated_points(cluster_points_df, cluster_points_filename, dataset_attributes,
                             predicted_label_for_current_day, true_label_df):
    """
    This is where we backup the points that are duplicated before it is removed.
    :param cluster_points_df: cluster points file as DataFrame
    :param cluster_points_filename: cluster points file name
    :param dataset_attributes: attributes of the cluster points
    :param predicted_label_for_current_day: dictionary containing cluster id mapping to a ground truth label
    :param true_label_df: grouth truth file as DataFrame
    :return: None
    """
    # First we want to get duplicates from true label, then stitch the cluster label using merge.
    true_label_dup = true_label_df[true_label_df.duplicated(subset=dataset_attributes, keep=False)]
    true_label_dup = true_label_dup.merge(cluster_points_df, on=dataset_attributes, how='inner')
    # Cosmetic renaming to allow consistency
    true_label_dup.rename(columns={'PopName': 'TrueLabel'}, inplace=True)
    # Get predicted label
    true_label_dup['PredictedLabel'] = true_label_dup['cluster_id'].map(predicted_label_for_current_day)
    # Round values to 5 dp. Stupid pandas mess it up
    tmp = true_label_dup.select_dtypes(include=[np.number])
    true_label_dup.loc[:, tmp.columns] = np.round(tmp, 5)
    true_label_dup.to_csv('{}/{}_duplicatedRows.csv'.format(backup_dir, cluster_points_filename.split('.')[0]),
                          index=False)


parse_config_file()
get_cluster_label_mapping_per_day()

pool = mp.Pool()
timepoints = list(data_per_timepoint.keys())

for t in timepoints:
    process_each_day(t)


