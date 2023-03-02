# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
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

import json
import os
from os.path import expanduser
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from nemo.utils import logging
from nemo.utils.data_utils import DataStoreObject, datastore_path_to_local_path, is_datastore_path


class ManifestBase:
    def __init__(self, *args, **kwargs):
        raise ValueError(
            "This class is deprecated, look at https://github.com/NVIDIA/NeMo/pull/284 for correct behaviour."
        )


class ManifestEN:
    def __init__(self, *args, **kwargs):
        raise ValueError(
            "This class is deprecated, look at https://github.com/NVIDIA/NeMo/pull/284 for correct behaviour."
        )


def item_iter(
    manifests_files: Union[str, List[str]], parse_func: Callable[[str, Optional[str]], Dict[str, Any]] = None
) -> Iterator[Dict[str, Any]]:
    """Iterate through json lines of provided manifests.

    NeMo ASR pipelines often assume certain manifest files structure. In
    particular, each manifest file should consist of line-per-sample files with
    each line being correct json dict. Each such json dict should have a field
    for audio file string, a field for duration float and a field for text
    string. Offset also could be additional field and is set to None by
    default.

    Args:
        manifests_files: Either single string file or list of such -
            manifests to yield items from.

        parse_func: A callable function which accepts as input a single line
            of a manifest and optionally the manifest file itself,
            and parses it, returning a dictionary mapping from str -> Any.

    Yields:
        Parsed key to value item dicts.

    Raises:
        ValueError: If met invalid json line structure.
    """

    if isinstance(manifests_files, str):
        manifests_files = [manifests_files]

    if parse_func is None:
        parse_func = __parse_item

    k = -1
    logging.debug('Manifest files: %s', str(manifests_files))
    for manifest_file in manifests_files:
        logging.debug('Using manifest file: %s', str(manifest_file))
        cached_manifest_file = DataStoreObject(manifest_file).get()
        logging.debug('Cached at: %s', str(cached_manifest_file))
        with open(expanduser(cached_manifest_file), 'r') as f:
            for line in f:
                k += 1
                item = parse_func(line, manifest_file)
                item['id'] = k

                yield item


def __parse_item(line: str, manifest_file: str) -> Dict[str, Any]:
    item = json.loads(line)

    # Audio file
    if 'audio_filename' in item:
        item['audio_file'] = item.pop('audio_filename')
    elif 'audio_filepath' in item:
        item['audio_file'] = item.pop('audio_filepath')
    elif 'audio_file' not in item:
        raise ValueError(
            f"Manifest file {manifest_file} has invalid json line structure: {line} without proper audio file key."
        )

    # If the audio path is a relative path and does not exist,
    # try to attach the parent directory of manifest to the audio path.
    # Revert to the original path if the new path still doesn't exist.
    # Assume that the audio path is like "wavs/xxxxxx.wav".
    item['audio_file'] = get_full_path(audio_file=item['audio_file'], manifest_file=manifest_file)

    # Duration.
    if 'duration' not in item:
        raise ValueError(
            f"Manifest file {manifest_file} has invalid json line structure: {line} without proper duration key."
        )

    # Text.
    if 'text' in item:
        pass
    elif 'text_filepath' in item:
        with open(item.pop('text_filepath'), 'r') as f:
            item['text'] = f.read().replace('\n', '')
    elif 'normalized_text' in item:
        item['text'] = item['normalized_text']
    else:
        item['text'] = ""

    # Optional RTTM file
    if 'rttm_file' in item:
        pass
    elif 'rttm_filename' in item:
        item['rttm_file'] = item.pop('rttm_filename')
    elif 'rttm_filepath' in item:
        item['rttm_file'] = item.pop('rttm_filepath')
    else:
        item['rttm_file'] = None
    if item['rttm_file'] is not None:
        item['rttm_file'] = get_full_path(audio_file=item['rttm_file'], manifest_file=manifest_file)

    # Optional audio feature file
    if 'feature_file' in item:
        pass
    elif 'feature_filename' in item:
        item['feature_file'] = item.pop('feature_filename')
    elif 'feature_filepath' in item:
        item['feature_file'] = item.pop('feature_filepath')
    else:
        item['feature_file'] = None
    if item['feature_file'] is not None:
        item['feature_file'] = get_full_path(audio_file=item['feature_file'], manifest_file=manifest_file)

    if 'is_valid' in item:
        item['is_valid'] = item.pop('is_valid')

    item = dict(
        audio_file=item['audio_file'],
        duration=item['duration'],
        text=item['text'],
        rttm_file=item['rttm_file'],
        feature_file=item['feature_file'],
        offset=item.get('offset', None),
        speaker=item.get('speaker', None),
        orig_sr=item.get('orig_sample_rate', None),
        token_labels=item.get('token_labels', None),
        lang=item.get('lang', None),
        is_valid=item.get('is_valid', None),
    )
    return item


def get_full_path(audio_file: str, manifest_file: str, audio_file_len_limit: int = 255) -> str:
    """Get full path to audio_file.

    If the audio_file is a relative path and does not exist,
    try to attach the parent directory of manifest to the audio path.
    Revert to the original path if the new path still doesn't exist.
    Assume that the audio path is like "wavs/xxxxxx.wav".

    Args:
        audio_file: path to an audio file, either absolute or assumed relative
                    to the manifest directory
        manifest_file: path to a manifest file
        audio_file_len_limit: limit for length of audio_file when using relative paths

    Returns:
        Full path to audio_file.
    """
    audio_file = Path(audio_file)

    if is_datastore_path(manifest_file):
        # WORKAROUND: pathlib does not support URIs, so use os.path
        manifest_dir = os.path.dirname(manifest_file)
    else:
        manifest_dir = Path(manifest_file).parent.as_posix()

    if (len(str(audio_file)) < audio_file_len_limit) and not audio_file.is_file() and not audio_file.is_absolute():
        # assume audio_file path is relative to manifest_dir
        audio_file_path = os.path.join(manifest_dir, audio_file.as_posix())

        if is_datastore_path(audio_file_path):
            # If audio was originally on an object store, use locally-cached path
            audio_file_path = datastore_path_to_local_path(audio_file_path)

        audio_file_path = Path(audio_file_path)

        if audio_file_path.is_file():
            audio_file = str(audio_file_path.absolute())
        else:
            audio_file = expanduser(audio_file)
    else:
        audio_file = expanduser(audio_file)
    return audio_file
