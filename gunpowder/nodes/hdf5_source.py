import logging

import numpy as np

from gunpowder.batch import Batch
from gunpowder.ext import h5py
from gunpowder.nodes.batch_provider import BatchProvider
from gunpowder.profiling import Timing
from gunpowder.provider_spec import ProviderSpec
from gunpowder.roi import Roi
from gunpowder.volume import VolumeType

logger = logging.getLogger(__name__)


class Hdf5Source(BatchProvider):

    def __init__(self, filename, raw_dataset, gt_dataset=None, gt_mask_dataset=None, resolution=None):

        self.filename = filename
        self.raw_dataset = raw_dataset
        self.gt_dataset = gt_dataset
        self.gt_mask_dataset = gt_mask_dataset
        self.specified_resolution = resolution

    def setup(self):

        f = h5py.File(self.filename, 'r')

        self.dims = None
        for ds in [self.raw_dataset, self.gt_mask_dataset, self.gt_mask_dataset]:

            if ds is None:
                continue

            if ds not in f:
                raise RuntimeError("%s not in %s"%(ds,self.filename))

            if self.dims is None:
                self.dims = f[ds].shape
            else:
                dims = f[ds].shape
                assert(len(dims) == len(self.dims))
                self.dims = tuple(min(self.dims[d], dims[d]) for d in range(len(dims)))

        f.close()

        self.spec = ProviderSpec()
        self.spec.roi = Roi(
                (0,)*len(self.dims),
                self.dims
        )

        if self.gt_mask_dataset is not None:
            with h5py.File(self.filename, 'r') as f:
                mask = np.array(f[self.gt_mask_dataset])
                good = np.where(mask > 0)
                min_good = tuple(np.min(good[d])     for d in range(len(self.dims)))
                max_good = tuple(np.max(good[d]) + 1 for d in range(len(self.dims)))
                self.spec.gt_roi = Roi(min_good, tuple(max_good[d] - min_good[d] for d in range(len(self.dims))))
                logger.info("GT ROI for source " + str(self) + ": " + str(self.spec.gt_roi))

        self.spec.has_gt = self.gt_dataset is not None
        self.spec.has_gt_mask = self.gt_mask_dataset is not None

    def get_spec(self):
        return self.spec

    def request_batch(self, batch_spec):

        timing = Timing(self)
        timing.start()

        spec = self.get_spec()

        if VolumeType.GT_LABELS in batch_spec.with_volumes and not spec.has_gt:
            raise RuntimeError("Asked for GT in a non-GT source.")

        if VolumeType.GT_MASK in batch_spec.with_volumes and not spec.has_gt_mask:
            raise RuntimeError("Asked for GT mask in a source that doesn't have one.")

        input_roi = batch_spec.input_roi
        output_roi = batch_spec.output_roi
        if not self.spec.roi.contains(input_roi):
            raise RuntimeError("Input ROI of batch %s outside of my ROI %s"%(input_roi,self.spec.roi))
        if not self.spec.roi.contains(output_roi):
            raise RuntimeError("Output ROI of batch %s outside of my ROI %s"%(output_roi,self.spec.roi))

        logger.debug("Filling batch request for input %s and output %s"%(str(input_roi),str(output_roi)))

        batch = Batch(batch_spec)
        batch.spec.resolution = self.resolution
        logger.debug("providing batch with resolution of {}".format(batch.spec.resolution))
        with h5py.File(self.filename, 'r') as f:
            logger.debug("Reading raw...")
            batch.volumes[VolumeType.RAW] = Volume(self.__read(f, self.raw_dataset, input_roi), interpolate=True)
            if VolumeType.GT_LABELS in batch.spec.with_volumes:
                logger.debug("Reading gt...")
                batch.volumes[VolumeType.GT_LABELS] = Volume(self.__read(f, self.gt_dataset, output_roi), interpolate=False)
            if VolumeType.GT_MASK in batch.spec.with_volumes:
                logger.debug("Reading gt mask...")
                batch.volumes[VolumeType.GT_MASK] = Volume(self.__read(f, self.gt_mask_dataset, output_roi), interpolate=False)

        logger.debug("done")

        timing.stop()
        batch.profiling_stats.add(timing)

        return batch

    def __read(self, f, ds, roi):

        return np.array(f[ds][roi.get_bounding_box()])

    def __repr__(self):

        return self.filename

    @property
    def resolution(self):
        if self.specified_resolution is not None:
            return self.specified_resolution
        else:
            try:
                with h5py.File(self.filename, 'r') as f:
                    return tuple(f[self.raw_dataset].attrs['resolution'])
            except KeyError:
                default_resolution = (1,) * len(self.dims)
                logger.warning("WARNING: your source does not contain resolution information"
                               " (no attribute 'resolution' in raw dataset). I will assume {}. "
                               "This might not be what you want.".format(default_resolution))
                return default_resolution
