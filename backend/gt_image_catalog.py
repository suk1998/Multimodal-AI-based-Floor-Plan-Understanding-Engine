"""Read-only inventory of user-provided composite change-location GT images.

An image annotation is valid GT evidence, but is not automatically a native-frame
space polygon, complete object inventory or a Space-Door-Space relation label.
"""
import hashlib
import struct
from pathlib import Path


def catalog_gt_images(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f'GT image directory not found: {directory}')
    images = []
    for path in sorted(directory.glob('*_Ground_Truth.png')):
        content = path.read_bytes()
        if len(content) < 24 or content[:8] != b'\x89PNG\r\n\x1a\n' or content[12:16] != b'IHDR':
            raise ValueError(f'Invalid PNG header: {path.name}')
        width, height = struct.unpack('>II', content[16:24])
        if not width or not height:
            raise ValueError(f'Invalid GT image dimensions: {path.name}')
        sha = hashlib.sha256(content).hexdigest()
        images.append({
            'variant': path.name.removesuffix('_Ground_Truth.png'),
            'path': str(path.resolve()), 'sha256': sha, 'width': width, 'height': height,
            'image_frame_id': f'gt_image:{sha}:{width}x{height}',
            'coordinate_system': 'GT_COMPOSITE_IMAGE_PIXELS',
            'annotation_kind': 'USER_PROVIDED_CHANGE_LOCATION_IMAGE',
            'status': 'IMAGE_GT_AVAILABLE',
            'source_pdf_binding': None, 'image_to_native_transform': None,
            'machine_readable_spatial_relation_annotations_available': False,
            'spatial_metrics': None,
            'note': 'Original image retained; no automatic native coordinates, space boundaries, connection labels or complete negative inventory inferred.',
        })
    if not images:
        raise ValueError(f'No *_Ground_Truth.png images found: {directory}')
    return {'schema_version': 'fpue_image_gt_catalog/1', 'directory': str(directory.resolve()), 'images': images, 'source_files_modified': False}
