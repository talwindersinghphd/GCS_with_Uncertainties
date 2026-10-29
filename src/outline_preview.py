"""Cumulative outline snapshots, projected independently of candidate scoring and Qt."""
from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
from sunpy.map import Map

from .search_scoring import display_boundaries


class OutlinePreview:
    def __init__(self, images, date):
        self.date = date
        self.views = {i: (image.data.shape, dict(image.meta)) for i, image in enumerate(images)}
        self.images = None
        self.reference = None
        self.cache = {}
        self.cancelled = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='GCS-preview')

    def submit(self, preview):
        return self.executor.submit(self.project, preview)

    def close(self):
        self.cancelled.set()
        self.executor.shutdown(wait=False)

    def project(self, preview):
        if self.cancelled.is_set():
            return None
        if self.images is None:
            # Use independent WCS objects; neither GUI maps nor full image arrays
            # are shared with the projection thread.
            self.images = {i: Map(np.broadcast_to(np.zeros((1, 1)), shape), metadata)
                           for i, (shape, metadata) in self.views.items()}
        config = preview['config']
        if self.reference is None:
            self.reference = display_boundaries(config['reference'], self.date, self.images,
                config['resolution'], config.get('mesh_density', 1),
                existing=preview['reference_boundaries'], cancelled=self.cancelled)
        cache = {}
        outlines = []
        for key, parameters, existing in zip(preview['overlay_keys'], preview['overlay_parameters'],
                                             preview['overlays']):
            if self.cancelled.is_set():
                return None
            if key in self.cache:
                boundary = self.cache[key]
            else:
                boundary = display_boundaries(parameters, self.date, self.images,
                    config['resolution'], config.get('mesh_density', 1), existing=existing,
                    cancelled=self.cancelled)
            cache[key] = boundary
            outlines.append(boundary)
        self.cache = cache
        if self.cancelled.is_set():
            return None
        return dict(reference_boundaries=self.reference, overlays=outlines,
                    overlay_keys=list(preview['overlay_keys']))
