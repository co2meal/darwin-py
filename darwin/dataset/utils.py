import itertools
import json
import multiprocessing as mp
import os
from collections import defaultdict
from pathlib import Path
from typing import Generator, Iterable, List, Optional

import numpy as np
from sklearn.model_selection import train_test_split
from tqdm import tqdm

from darwin.utils import SUPPORTED_IMAGE_EXTENSIONS, is_image_extension_allowed


def extract_classes(annotations_path: Path, annotation_type: str):
    """
    Given a the GT as json files extracts all classes and an maps images index to classes

    Parameters
    ----------
    annotations_files: Path
        Path to the json files with the GT information of each image
    annotation_type : str
        Type of annotation to use to extract the Gt information

    Returns
    -------
    classes: dict
    Dictionary where keys are the classes found in the GT and values
    are a list of file numbers which contain it
    idx_to_classes: dict
    Dictionary where keys are image indices and values are all classes
    contained in that image
    """
    classes = defaultdict(set)
    indices_to_classes = defaultdict(set)
    annotation_files = list(annotations_path.glob("*.json"))
    for i, file_name in enumerate(annotation_files):
        with open(file_name) as f:
            annotations = json.load(f)["annotations"]
            if annotations:
                for annotation in annotations:
                    if annotation_type not in annotation:
                        continue
                    class_name = annotation["name"]
                    indices_to_classes[i].add(class_name)
                    classes[class_name].add(i)
    return classes, indices_to_classes


def make_class_lists(dataset):
    """
    Support function to extract classes and save the output to file

    Parameters
    ----------
    dataset
        Path to the location of the dataset on the file system
    """
    assert dataset is not None
    if isinstance(dataset, Path) or isinstance(dataset, str):
        dataset_path = Path(dataset)
    else:
        dataset_path = dataset.local_path

    annotations_path = dataset_path / "annotations"
    assert annotations_path.exists()
    lists_path = dataset_path / "lists"
    lists_path.mkdir(exist_ok=True)

    for annotation_type in ["tag", "polygon"]:
        fname = lists_path / f"classes_{annotation_type}.txt"
        classes, _ = extract_classes(annotations_path, annotation_type=annotation_type)
        classes_names = list(classes.keys())
        if len(classes_names) > 0:
            classes_names.sort()
            with open(str(fname), "w") as f:
                f.write("\n".join(classes_names))


def get_classes(dataset, annotation_type: str, remove_background: bool = True):
    """
    Given a dataset and an annotation_type returns the list of classes

    Parameters
    ----------
    dataset
        Path to the location of the dataset on the file system
    classes_type
        The type of annotation classes [tag, polygon]
    remove_background
        Removes the background class (if exists) from the list of classes

    Returns
    -------
    classes: list
        List of classes in the dataset of type classes_type
    """

    assert dataset is not None
    if isinstance(dataset, Path) or isinstance(dataset, str):
        dataset_path = Path(dataset)
    else:
        dataset_path = dataset.local_path

    classes_file = f"classes_{annotation_type}.txt"
    classes = [e.strip() for e in open(dataset_path / "lists" / classes_file)]
    if remove_background and classes[0] == "__background__":
        classes = classes[1:]
    return classes


def _write_to_file(annotation_files: List, file_path: Path, split_idx: Iterable):
    """Support function for writing split indices to file

    Parameters
    ----------
    annotation_files : list
        List of json files with the GT information of each image
    file_path : Path
        Path to the file where to save the list of indices
    split_idx : Iterable
        Indices of files for this split
    """
    with open(str(file_path), "w") as f:
        for i in split_idx:
            f.write(f"{annotation_files[i].stem}\n")


def remove_cross_contamination(X_a: np.ndarray, X_b: np.ndarray, y_a: np.ndarray, y_b: np.ndarray):
    """
    Remove cross contamination present in X_a and X_b by selecting one or the other on a flip coin decision.

    The reason of cross contamination existence is
        expanded_list = [(k, c) for k, v in idx_to_classes.items() for c in v]
    in _stratify_samples(). This line creates as many entries for an image as there are lables
    attached to it. For this reason it can be that the stratification algorithm splits
    the image in both sets, A and B.
    This is very bad and this function addressed exactly that issue, removing duplicates from
    either A or B.

    Parameters
    ----------
    X_a : ndarray
    X_b : ndarray
        Arrays of elements to remove cross contamination from
    y_a : ndarray
    y_b : ndarray
        Arrays of labels relative to X_a and X_b to be filtered in the same fashion
    Returns
    -------
    X_a, X_b, y_a, y_b : ndarray
        All input parameters filtered by removing cross contamination across A and B
    """
    for a in X_a:
        if a in X_b:
            # Remove from A or B based on random chance
            if np.random.rand() > 0.5:
                # Remove ALL entries from A
                keep_locations = X_a != a
                X_a = X_a[keep_locations]
                y_a = y_a[keep_locations]
            else:
                # Remove ALL entries from B
                keep_locations = X_b != a
                X_b = X_b[keep_locations]
                y_b = y_b[keep_locations]
    return X_a, X_b, y_a, y_b


def _stratify_samples(idx_to_classes, split_seed, test_percentage, val_percentage):
    """Splits the list of indices into train, val and test according to their labels (stratified)

    Parameters
    ----------
    idx_to_classes: dict
    Dictionary where keys are image indices and values are all classes
    contained in that image
    split_seed : int
        Seed for the randomness
    val_percentage : float
        Percentage of images used in the validation set
    test_percentage : float
        Percentage of images used in the test set

    Returns
    -------
    X_train, X_val, X_test : list
        List of indices of the images for each split
    """

    # Expand the list of files with all the classes
    expanded_list = [(k, c) for k, v in idx_to_classes.items() for c in v]
    # Stratify
    file_indices, labels = zip(*expanded_list)
    file_indices, labels = np.array(file_indices), np.array(labels)
    # Extract entries whose support set is 1 (it would make sklearn crash) and append the to train later
    unique_labels, count = np.unique(labels, return_counts=True)
    single_files = []
    for l in unique_labels[count == 1]:
        index = np.where(labels == l)[0][0]
        single_files.append(file_indices[index])
        labels = np.delete(labels, index)
        file_indices = np.delete(file_indices, index)
    # If file_indices or labels are empty, the following train_test_split will crash (empty train set)
    if len(file_indices) == 0 or len(labels) == 0:
        return [], [], []

    X_train, X_tmp, y_train, y_tmp = remove_cross_contamination(
        *train_test_split(
            np.array(file_indices),
            np.array(labels),
            test_size=int((val_percentage + test_percentage) * 100) / 100,
            random_state=split_seed,
            stratify=labels,
        )
    )
    # Append files whose support set is 1 to train
    X_train = np.concatenate((X_train, np.array(single_files)), axis=0)

    if test_percentage == 0.0:
        return list(set(X_train.astype(np.int))), list(set(X_tmp.astype(np.int))), None

    X_val, X_test, y_val, y_test = remove_cross_contamination(
        *train_test_split(
            X_tmp,
            y_tmp,
            test_size=(test_percentage * 100 / (val_percentage + test_percentage)) / 100,
            random_state=split_seed,
            stratify=y_tmp,
        )
    )

    # Remove duplicates within the same set
    # NOTE: doing that earlier (e.g. in remove_cross_contamination()) would produce mathematical
    # mistakes in the class balancing between validation and test sets.
    return (
        list(set(X_train.astype(np.int))),
        list(set(X_val.astype(np.int))),
        list(set(X_test.astype(np.int))),
    )


def split_dataset(
    dataset,
    val_percentage: Optional[float] = 0.1,
    test_percentage: Optional[float] = 0.2,
    force_resplit: Optional[bool] = False,
    split_seed: Optional[int] = 0,
    make_default_split: Optional[bool] = True,
):
    """
    Given a local a dataset (pulled from Darwin) creates lists of file names
    for each split for train, validation, and test.

    Parameters
    ----------
    dataset : RemoteDataset or Path
        It can be either a Darwin Dataset or local path to the dataset
    val_percentage : float
        Percentage of images used in the validation set
    test_percentage : float
        Percentage of images used in the test set
    force_resplit : bool
        Discard previous split and create a new one
    split_seed : int
        Fix seed for random split creation
    make_default_split: bool
        Makes this split the default split

    Returns
    -------
    splits : dict
        Keys are the different splits (random, tags, ...) and values are the relative file names
    """
    assert dataset is not None
    if isinstance(dataset, Path) or isinstance(dataset, str):
        dataset_path = Path(dataset)
    else:
        dataset_path = dataset.local_path

    annotation_path = dataset_path / "annotations"
    assert annotation_path.exists()
    annotation_files = list(annotation_path.glob("*.json"))

    # Prepare the lists folder
    lists_path = dataset_path / "lists"
    lists_path.mkdir(parents=True, exist_ok=True)

    # Create split id, path and final split paths
    if val_percentage is None or not 0 < val_percentage < 1.0:
        raise ValueError(
            f"Invalid validation percentage ({val_percentage}). " f"Must be > 0 and < 1.0"
        )
    if test_percentage is None or not 0 <= test_percentage < 1.0:
        raise ValueError(f"Invalid test percentage ({test_percentage}). " f"Must be > 0 and < 1.0")
    if not val_percentage + test_percentage < 1.0:
        raise ValueError(
            f"Invalid combination of validation ({val_percentage}) "
            f"and test ({test_percentage}) percentages. Their sum must be < 1.0"
        )
    if split_seed is None:
        raise ValueError("Seed is None")
    split_id = f"split_v{int(val_percentage*100)}_t{int(test_percentage*100)}"
    if split_seed != 0:
        split_id += "_s{split_seed}"
    split_path = lists_path / split_id

    # Prepare the return value with the paths of the splits
    splits = {}
    splits["random"] = {
        "train": Path(split_path / "random_train.txt"),
        "val": Path(split_path / "random_val.txt"),
    }
    splits["stratified_tag"] = {
        "train": Path(split_path / "stratified_tag_train.txt"),
        "val": Path(split_path / "stratified_tag_val.txt"),
    }
    splits["stratified_polygon"] = {
        "train": Path(split_path / "stratified_polygon_train.txt"),
        "val": Path(split_path / "stratified_polygon_val.txt"),
    }
    if test_percentage > 0.0:
        splits["random"]["test"] = Path(split_path) / "random_test.txt"
        splits["stratified_tag"]["test"] = Path(split_path / "stratified_tag_test.txt")
        splits["stratified_polygon"]["test"] = Path(split_path / "stratified_polygon_test.txt")

    # Do the actual split
    if not split_path.exists():
        os.makedirs(str(split_path), exist_ok=True)

        # RANDOM SPLIT
        # Compute split sizes
        dataset_size = sum(1 for _ in annotation_files)
        val_size = int(dataset_size * val_percentage)
        test_size = int(dataset_size * test_percentage)
        train_size = dataset_size - val_size - test_size
        # Slice a permuted array as big as the dataset
        np.random.seed(split_seed)
        indices = np.random.permutation(dataset_size)
        train_indices = list(indices[:train_size])
        val_indices = list(indices[train_size : train_size + val_size])
        test_indices = list(indices[train_size + val_size :])
        # Write files
        _write_to_file(annotation_files, splits["random"]["train"], train_indices)
        _write_to_file(annotation_files, splits["random"]["val"], val_indices)
        if test_percentage > 0.0:
            _write_to_file(annotation_files, splits["random"]["test"], test_indices)

        # STRATIFIED SPLIT ON TAGS
        # Stratify
        classes_tag, idx_to_classes_tag = extract_classes(annotation_path, "tag")
        if len(idx_to_classes_tag) > 0:
            train_indices, val_indices, test_indices = _stratify_samples(
                idx_to_classes_tag, split_seed, test_percentage, val_percentage
            )
            # Write files
            _write_to_file(annotation_files, splits["stratified_tag"]["train"], train_indices)
            _write_to_file(annotation_files, splits["stratified_tag"]["val"], val_indices)
            if test_percentage > 0.0:
                _write_to_file(annotation_files, splits["stratified_tag"]["test"], test_indices)

        # STRATIFIED SPLIT ON POLYGONS
        # Stratify
        classes_polygon, idx_to_classes_polygon = extract_classes(annotation_path, "polygon")
        if len(idx_to_classes_polygon) > 0:
            train_indices, val_indices, test_indices = _stratify_samples(
                idx_to_classes_polygon, split_seed, test_percentage, val_percentage
            )
            # Write files
            _write_to_file(annotation_files, splits["stratified_polygon"]["train"], train_indices)
            _write_to_file(annotation_files, splits["stratified_polygon"]["val"], val_indices)
            if test_percentage > 0.0:
                _write_to_file(annotation_files, splits["stratified_polygon"]["test"], test_indices)

    # Create symlink for default split
    split = lists_path / "split"
    if make_default_split or not split.exists():
        if split.exists():
            split.unlink()
        split.symlink_to(lists_path / split_id)

    return splits


def _f(x):
    """Support function for pool.map() in _exhaust_generator()"""
    if callable(x):
        return x()


def exhaust_generator(progress: Generator, count: int, multi_threaded: bool):
    """Exhausts the generator passed as parameter. Can be done multi threaded if desired

    Parameters
    ----------
    progress : Generator
        Generator to exhaust
    count : int
        Size of the generator
    multi_threaded : bool
        Flag for multi-threaded enabled operations

    Returns
    -------
    List[dict]
        List of responses from the generator execution
    """
    responses = []
    if multi_threaded:
        pbar = tqdm(total=count)

        def update(*a):
            pbar.update()

        with mp.Pool(mp.cpu_count()) as pool:
            for f in progress:
                responses.append(pool.apply_async(_f, args=(f,), callback=update))
            pool.close()
            pool.join()
        responses = [response.get() for response in responses if response.successful()]
    else:
        for f in tqdm(progress, total=count, desc="Progress"):
            responses.append(_f(f))
    return responses


def get_annotations(
    dataset,
    partition: str,
    split: str = "split",
    split_type: str = "stratified",
    annotation_type: str = "polygon",
):
    """
    Returns all the annotations of a given dataset and split in a single dictionary

    Parameters
    ----------
    dataset
        Path to the location of the dataset on the file system
    partition
        Selects one of the partitions [train, val, test]
    split
        Selects the split that defines the percetages used (use 'split' to select the default split
    split_type
        Heuristic used to do the split [random, stratified]
    annotation_type
        The type of annotation classes [tag, polygon]

    Returns
    -------
    dict
        Dictionary containing all the annotations of the dataset
    """
    assert dataset is not None
    if isinstance(dataset, Path) or isinstance(dataset, str):
        dataset_path = Path(dataset)
    else:
        dataset_path = dataset.local_path

    if partition not in ["train", "val", "test"]:
        raise ValueError("partition should be either 'train', 'val', or 'test'")
    if split_type not in ["random", "stratified"]:
        raise ValueError("split_type should be either 'random' or 'stratified'")
    if annotation_type not in ["tag", "polygon"]:
        raise ValueError("annotation_type should be either 'tag' or 'polygon'")

    # Get the list of classes
    classes = get_classes(dataset, annotation_type=annotation_type, remove_background=True)
    # Get the split
    if split_type == "random":
        split_file = f"{split_type}_{partition}.txt"
    elif split_type == "stratified":
        split_file = f"{split_type}_{annotation_type}_{partition}.txt"
    split_path = dataset_path / "lists" / split / split_file
    stems = (e.strip() for e in split_path.open())
    images_path = []
    annotations_path = []

    # Find all the annotations and their corresponding images
    for stem in stems:
        annotation_path = dataset_path / f"annotations/{stem}.json"
        images = [
            image
            for image in dataset_path.glob(f"images/{stem}.*")
            if is_image_extension_allowed(image.suffix)
        ]
        if len(images) < 1:
            raise ValueError(
                f"Annotation ({annotation_path}) does" f" not have a corresponding image"
            )
        if len(images) > 1:
            raise ValueError(
                f"Image ({stem}) is present with multiple extensions." f" This is forbidden."
            )
        assert len(images) == 1
        image_path = images[0]
        images_path.append(image_path)
        annotations_path.append(annotation_path)

    if len(images_path) == 0:
        raise ValueError(
            f"Could not find any {SUPPORTED_IMAGE_EXTENSIONS} file" f" in {dataset_path / 'images'}"
        )

    assert len(images_path) == len(annotations_path)

    try:
        from detectron2.structures import BoxMode
    except ImportError:
        BoxMode = None

    # Load and re-format all the annotations
    dataset_dicts = []
    for image_id, (im_path, annot_path) in enumerate(zip(images_path, annotations_path)):
        record = {}

        with annot_path.open() as f:
            data = json.load(f)

        height, width = data["image"]["height"], data["image"]["width"]
        annotations = data["annotations"]

        filename = im_path
        record["file_name"] = str(filename)
        record["height"] = height
        record["width"] = width
        record["image_id"] = image_id

        objs = []
        for obj in annotations:
            px, py = [], []
            if "polygon" not in obj:
                continue
            for point in obj["polygon"]["path"]:
                px.append(point["x"])
                py.append(point["y"])
            poly = [(x, y) for x, y in zip(px, py)]
            if len(poly) < 3:  # Discard polyhons with less than 3 points
                continue
            poly = list(itertools.chain.from_iterable(poly))

            category_id = classes.index(obj["name"])

            if BoxMode is not None:
                box_mode = BoxMode.XYXY_ABS
            else:
                box_mode = 0

            obj = {
                "bbox": [np.min(px), np.min(py), np.max(px), np.max(py)],
                "bbox_mode": box_mode,
                "segmentation": [poly],
                "category_id": category_id,
                "iscrowd": 0,
            }
            objs.append(obj)
        record["annotations"] = objs
        dataset_dicts.append(record)
    return dataset_dicts
