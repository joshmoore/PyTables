# Eh! python!, We are going to include isolatin characters here
# -*- coding: latin-1 -*-

########################################################################
#
#       License: BSD
#       Created: November 12, 2003
#       Author:  Francesc Altet - faltet@carabos.com
#
#       $Id$
#
########################################################################

"""Here is defined the VLArray class

See VLArray class docstring for more info.

Classes:

    VLArray

Functions:


Misc variables:

    __version__


"""

import sys
import warnings
import cPickle

import numpy

import tables.hdf5Extension as hdf5Extension
from tables.utils import processRangeRead, convertToNPAtom, convToFlavor, \
     idx2long, byteorders, calcBufferSize
from tables.Atom import Atom, ObjectAtom, VLStringAtom, StringAtom, EnumAtom
from tables.Leaf import Leaf
from tables.constants import CHUNKTIMES


__version__ = "$Revision$"

# default version for VLARRAY objects
#obversion = "1.0"    # initial version
#obversion = "1.0"    # add support for complex datatypes
#obversion = "1.1"    # This adds support for time datatypes.
obversion = "1.2"    # This adds support for enumerated datatypes.


class VLArray(hdf5Extension.VLArray, Leaf):
    """Represent a variable length (ragged) array in HDF5 file.

    It enables to create new datasets on-disk from NumPy, Numeric,
    numarray, lists, tuples, strings or scalars, or open existing
    ones. The datasets are made of records that are made of a variable
    length number of atomic objects (which has to have always the same
    shape).

    All NumPy, Numeric and numarray typecodes are supported except for
    complex datatypes.

    Methods:

        append(sequence)
        read(start, stop, step)
        __iter__()
        iterrows(start, stop, step)
        __getitem__(slice)
        __setitem__(slice, value)

    Instance variables:

        atom -- The class instance choosed for the atomic object
        nrow -- On iterators, this is the index of the row currently
            dealed with
        nrows -- The total number of rows
        shape -- The shape of self (expressed as (self.nrows,))
        byteorder -- The byte ordering of atoms in self

    """

    # Class identifier.
    _c_classId = 'VLARRAY'


    # <properties>
    shape = property(
        lambda self: (self.nrows,), None, None,
        "The shape of the stored array.")
    byteorder = property(
        lambda self: byteorders[self.atom.dtype.byteorder], None, None,
        "The endianness of data in memory ('big', 'little' or 'irrelevant').")
    # </properties>


    def __init__(self, parentNode, name,
                 atom=None, title="",
                 filters=None, expectedsizeinMB=1.0,
                 _log=True):
        """Create the instance Array.

        Keyword arguments:

        atom -- An Atom object representing the shape, type and
            flavor of the atomic objects to be saved.

        title -- Sets a TITLE attribute on the HDF5 array entity.

        filters -- An instance of the Filters class that provides
            information about the desired I/O filters to be applied
            during the life of this object.

        expectedsizeinMB -- An user estimate about the size (in MB) of
            the final VLArray object. If not provided, the default
            value is 1 MB.  If you plan to create both much smaller or
            much bigger Arrays try providing a guess; this will
            optimize the HDF5 B-Tree creation and management process
            time and the amount of memory used.

        """

        self._v_version = None
        """The object version of this array."""

        self._v_new = new = atom is not None
        """Is this the first time the node has been created?"""
        self._v_new_title = title
        """New title for this node."""
        self._v_new_filters = filters
        """New filter properties for this array."""
        self._v_expectedsizeinMB = expectedsizeinMB
        """The expected size of the array in MiB."""

        self._v_maxTuples = 100       # maybe enough for most applications
        """The maximum number of rows that are read on each chunk iterator."""
        self._v_chunksize = None
        """The HDF5 chunk size for ``VLArray`` objects."""

        # Miscellaneous iteration rubbish.
        self._start = None
        """Starting row for the current iteration."""
        self._stop = None
        """Stopping row for the current iteration."""
        self._step = None
        """Step size for the current iteration."""
        self._nrowsread = None
        """Number of rows read up to the current state of iteration."""
        self._startb = None
        """Starting row for current buffer."""
        self._stopb = None
        """Stopping row for current buffer. """
        self._row = None
        """Current row in iterators (sentinel)."""
        self._init = False
        """Whether we are in the middle of an iteration or not (sentinel)."""
        self.listarr = None
        """Current buffer in iterators."""

        # Documented (*public*) attributes.
        self.atom = atom
        """
        An `Atom` instance representing the shape, type and flavor of
        the atomic objects to be saved.
        """
        self.nrow = None
        """On iterators, this is the index of the current row."""
        self.nrows = None
        """The total number of rows."""

        super(VLArray, self).__init__(parentNode, name, new, filters, _log)


    def _calcChunksize(self, expectedsizeinMB):
        """Calculate the maxTuples for a buffer and HDF5 chunk size."""

        expectedsizeinKB = expectedsizeinMB * 1024
        buffersize = calcBufferSize(expectedsizeinKB)

        # For computing the chunksize for HDF5 VL types, we have to
        # choose the itemsize of the *each* element of the atom
        # and not the size of the entire atom.
        # F. Altet 2006-11-23
        elemsize = self.atom.dtype.itemsize
        # Set the chunksize
        chunksize = buffersize // (elemsize * CHUNKTIMES)
        # Safeguard against atoms being extremely large
        if chunksize == 0:
            chunksize = 1
        return chunksize


    def _g_create(self):
        """Create a variable length array (ragged array)."""

        self._v_version = obversion
        # Check for zero dims in atom shape (not allowed in VLArrays)
        zerodims = numpy.sum(numpy.array(self.atom.shape) == 0)
        if zerodims > 0:
            raise ValueError, \
"""When creating VLArrays, none of the dimensions of the Atom instance can
be zero."""

        self._atomicdtype = self.atom.dtype
        self._atomicptype = self.atom.ptype
        self._atomicshape = self.atom.shape
        self._atomicsize = self.atom.atomsize()
        self._basesize = self.atom.dtype.itemsize
        self.flavor = self.atom.flavor

        # Compute the optimal chunksize
        self._v_chunkshape = self._calcChunksize(self._v_expectedsizeinMB)
        self.nrows = 0     # No rows at creation time

        self._v_objectID = self._createArray(self._v_new_title)
        return self._v_objectID


    def _g_open(self):
        """Get the metadata info for an array in file."""

        (self._v_objectID, self.nrows, self.flavor) = self._openArray()

        flavor = self.flavor
        ptype = self._atomicptype

        # First, check the special cases VLString and Object types
        if flavor == "VLString":
            self.atom = VLStringAtom()
        elif flavor == "Object":
            self.atom = ObjectAtom()
        elif ptype == 'String':
            self.atom = StringAtom(self._atomicshape, self._basesize,
                                   flavor, warn=False)
        elif ptype == 'Enum':
            (enum, type_) = self._g_loadEnum()
            self.atom = EnumAtom(enum, type_, self._atomicshape,
                                 flavor, warn=False)
            self._atomicdtype = type_
        else:
            self.atom = Atom(ptype, self._atomicshape, flavor, warn=False)

        return self._v_objectID


    def _checkShape(self, nparr):
        # Check for zero dimensionality array
        zerodims = numpy.sum(numpy.array(nparr.shape) == 0)
        if zerodims > 0:
            # No objects to be added
            return 0
        shape = nparr.shape
        atom_shape = self.atom.shape
        shapelen = len(nparr.shape)
        if isinstance(atom_shape, tuple):
            atomshapelen = len(self.atom.shape)
        else:
            atom_shape = (self.atom.shape,)
            atomshapelen = 1
        diflen = shapelen - atomshapelen
        if shape == atom_shape:
            nobjects = 1
        elif (diflen == 1 and shape[diflen:] == atom_shape):
            # Check if the leading dimensions are all ones
            #if shape[:diflen-1] == (1,)*(diflen-1):
            #    nobjects = shape[diflen-1]
            #    shape = shape[diflen:]
            # It's better to accept only inputs with the exact dimensionality
            # i.e. a dimensionality only 1 element larger than atom
            nobjects = shape[0]
            shape = shape[1:]
        elif atom_shape == (1,) and shapelen == 1:
            # Case where shape = (N,) and shape_atom = 1 or (1,)
            nobjects = shape[0]
        else:
            raise ValueError, \
"""The object '%s' is composed of elements with shape '%s', which is not compatible with the atom shape ('%s').""" % \
(nparr, shape, atom_shape)
        return nobjects


    def getEnum(self):
        """
        Get the enumerated type associated with this array.

        If this array is of an enumerated type, the corresponding `Enum`
        instance is returned.  If it is not of an enumerated type, a
        ``TypeError`` is raised.
        """

        if self.atom.ptype != 'Enum':
            raise TypeError("array ``%s`` is not of an enumerated type"
                            % self._v_pathname)

        return self.atom.enum


    def append(self, sequence, *objects):
        """
        Append objects in the `sequence` to the array.

        This method appends the objects in the `sequence` to a *single
        row* in this array.  The type of individual objects must be
        compliant with the type of atoms in the array.  In the case of
        variable length strings, the very string to append is the
        `sequence`.

        Example of use (code available in ``examples/vlarray1.py``)::

            import tables
            from numpy import *   # or, from numarray import *

            # Create a VLArray:
            fileh = tables.openFile("vlarray1.h5", mode = "w")
            vlarray = fileh.createVLArray(fileh.root, 'vlarray1',
            tables.Int32Atom(flavor="Numeric"),
                             "ragged array of ints", Filters(complevel=1))
            # Append some (variable length) rows:
            vlarray.append(array([5, 6]))
            vlarray.append(array([5, 6, 7]))
            vlarray.append([5, 6, 9, 8])

            # Now, read it through an iterator:
            for x in vlarray:
                print vlarray.name+"["+str(vlarray.nrow)+"]-->", x

            # Close the file
            fileh.close()

        The output of the previous program looks like this::

            vlarray1[0]--> [5 6]
            vlarray1[1]--> [5 6 7]
            vlarray1[2]--> [5 6 9 8]

        The `objects` argument is only retained for backwards
        compatibility; please do *not* use it.
        """

        self._v_file._checkWritable()

        isseq = True
        try:  # fastest check in most cases
            len(sequence)
        except TypeError:
            isseq = False

        if not isseq or len(objects) > 0:
            warnings.warn(DeprecationWarning("""\
using multiple arguments with ``append()`` is *strongly deprecated*; \
please put them in a single sequence object"""),
                          stacklevel=2)
            # This is not optimum, but neither frequent.
            object = (sequence,) + tuple(objects)
        else:
            object = sequence
        # After that, `object` is assured to be a sequence.

        # Prepare the object to convert it into a NumPy object
        if self.atom.flavor == "Object":
            # Special case for a generic object
            # (to be pickled and saved as an array of unsigned bytes)
            buf = cPickle.dumps(object, 0)
            object = numpy.ndarray(buffer=buf, dtype='uint8', shape=len(buf))
        elif self.atom.flavor == "VLString":
            # Special case for a generic object
            # (to be pickled and saved as an array of unsigned bytes)
            if type(object) not in (str,unicode):
                raise TypeError, \
"""The object "%s" is not of type String or Unicode.""" % (str(object))
            try:
                object = object.encode('utf-8')
            except UnicodeError, ue:
                raise ValueError, "Problems when converting the object '%s' to the encoding 'utf-8'. The error was: %s" % (object, ue)
            object = numpy.ndarray(buffer=object, dtype='uint8', shape=len(object))

        if len(object) > 0:
            # The object needs to be copied to make the operation safe
            # to in-place conversion.
            copy = self._atomicptype in ['Time64']
            nparr = convertToNPAtom(object, self.atom, copy)
            nobjects = self._checkShape(nparr)
        else:
            nobjects = 0
            nparr = None

        self._append(nparr, nobjects)
        self.nrows += 1


    def iterrows(self, start=None, stop=None, step=None):
        """Iterate over all the rows or a range.

        """

        (self._start, self._stop, self._step) = \
                     processRangeRead(self.nrows, start, stop, step)
        self._initLoop()
        return self


    def __iter__(self):
        """Iterate over all the rows."""

        if not self._init:
            # If the iterator is called directly, assign default variables
            self._start = 0
            self._stop = self.nrows
            self._step = 1
            # and initialize the loop
            self._initLoop()
        return self


    def _initLoop(self):
        "Initialization for the __iter__ iterator"

        self._nrowsread = self._start
        self._startb = self._start
        self._row = -1   # Sentinel
        self._init = True  # Sentinel
        self.nrow = self._start - self._step    # row number


    def next(self):
        "next() method for __iter__() that is called on each iteration"
        if self._nrowsread >= self._stop:
            self._init = False
            raise StopIteration        # end of iteration
        else:
            # Read a chunk of rows
            if self._row+1 >= self._v_maxTuples or self._row < 0:
                self._stopb = self._startb+self._step*self._v_maxTuples
                self.listarr = self.read(self._startb, self._stopb, self._step)
                self._row = -1
                self._startb = self._stopb
            self._row += 1
            self.nrow += self._step
            self._nrowsread += self._step
            return self.listarr[self._row]


    def __getitem__(self, key):
        """Returns a vlarray row or slice.

        It takes different actions depending on the type of the "key"
        parameter:

        If "key"is an integer, the corresponding row is returned. If
        "key" is a slice, the row slice determined by key is returned.

        """

        if type(key) in (int,long) or isinstance(key, numpy.integer):
            if key >= self.nrows:
                raise IndexError, "Index out of range"
            if key < 0:
                # To support negative values
                key += self.nrows
            return self.read(key)[0]
        elif isinstance(key, slice):
            return self.read(key.start, key.stop, key.step)
        else:
            raise IndexError, "Non-valid index or slice: %s" % \
                  key


    def __setitem__(self, keys, value):
        """Updates a vlarray row "keys" by setting it to "value".

        If "keys" is an integer, it refers to the number of row to be
        modified.

        If "keys" is a tuple, the first element refers to the row
        to be modified, and the second element to the range (so, it
        can be an integer or an slice) of the row that will be
        updated.

        Note: When updating VLStrings (codification UTF-8) or Objects,
        there is a problem: we can only update values with *exactly*
        the same bytes than in the original row. With UTF-8 encoding
        this is problematic because, for instance, 'c' takes 1 byte,
        but '�' takes at least two (!). Perhaps another codification
        does not have this problem, I don't know. With objects, the
        same happens, because cPickle applied on an instance (for
        example) does not guarantee to return the same number of bytes
        than over other instance, even of the same class than the
        former. This effectively limits the number of objects than can
        be updated in VLArrays, most specially VLStrings and Objects
        as has been said before.

        """

        self._v_file._checkWritable()

        if not isinstance(keys, tuple):
            keys = (keys, None)
        if len(keys) > 2:
            raise IndexError, "You cannot specify more than two dimensions"
        nrow, rng = keys
        # Process the first index
        if not (type(nrow) in (int,long) or isinstance(nrow, numpy.integer)):
            raise IndexError, "The first dimension only can be an integer"
        if nrow >= self.nrows:
            raise IndexError, "First index out of range"
        if nrow < 0:
            # To support negative values
            nrow += self.nrows
        # Process the second index
        if type(rng) in (int,long) or isinstance(rng, numpy.integer):
            start = rng; stop = start+1; step = 1
        elif isinstance(rng, slice):
            start, stop, step = rng.start, rng.stop, rng.step
        elif rng is None:
            start, stop, step = None, None, None
        else:
            raise IndexError, "Non-valid second index or slice: %s" % rng

        object = value
        # Prepare the object to convert it into a NumPy object
        if self.atom.flavor == "Object":
            # Special case for a generic object
            # (to be pickled and saved as an array of unsigned bytes)
            buf = cPickle.dumps(object, 0)
            object = numpy.ndarray(buffer=buf, dtype='uint8', shape=len(buf))
        elif self.atom.flavor == "VLString":
            # Special case for a generic object
            # (to be pickled and saved as an array of unsigned bytes)
            if type(object) not in (str,unicode):
                raise TypeError, \
"""The object "%s" is not of type String or Unicode.""" % (str(object))
            try:
                object = object.encode('utf-8')
            except UnicodeError, ue:
                raise ValueError, "Problems when converting the object '%s' to the encoding 'utf-8'. The error was: %s" % (object, ue)
            object = numpy.ndarray(buffer=object, dtype='uint8', shape=len(object))

        value = convertToNPAtom(object, self.atom)
        nobjects = self._checkShape(value)

        # Get the previous value
        nrow = idx2long(nrow)   # To convert any possible numpy scalar value
        nparr = self._readArray(nrow, nrow+1, 1)[0]
        nobjects = len(nparr)
        if len(value) > nobjects:
            raise ValueError, \
"Length of value (%s) is larger than number of elements in row (%s)" % \
(len(value), nobjects)
        # Assign the value to it
        # The values can be numpy scalars. Convert them before building the slice.
        if start is not None: start = idx2long(start)
        if stop is not None: stop = idx2long(stop)
        if step is not None: step = idx2long(step)
        try:
            nparr[slice(start, stop, step)] = value
        except Exception, exc:  #XXX
            raise ValueError, \
"Value parameter:\n'%r'\ncannot be converted into an array object compliant vlarray[%s] row: \n'%r'\nThe error was: <%s>" % \
        (value, keys, nparr[slice(start, stop, step)], exc)

        if nparr.size > 0:
            self._modify(nrow, nparr, nobjects)


    # Accessor for the _readArray method in superclass
    def read(self, start=None, stop=None, step=1):
        """Read the array from disk and return it as a self.flavor object."""

        start, stop, step = processRangeRead(self.nrows, start, stop, step)
        if start == stop:
            listarr = []
        else:
            listarr = self._readArray(start, stop, step)

        if self.flavor <> "numpy":
            # Convert the list to the right flavor
            outlistarr = [ convToFlavor(self, arr, "VLArray")
                           for arr in listarr ]
            if self.flavor == "Tuple":
                outlistarr = tuple(outlistarr)
        else:
            # 'numpy' flavor does not need additional conversion
            outlistarr = listarr
        return outlistarr


    def _g_copyWithStats(self, group, name, start, stop, step,
                         title, filters, _log):
        "Private part of Leaf.copy() for each kind of leaf"

        # Build the new VLArray object
        object = VLArray(
            group, name, self.atom, title=title, filters=filters,
            expectedsizeinMB=self._v_expectedsizeinMB, _log=_log)
        # Now, fill the new vlarray with values from the old one
        # This is not buffered because we cannot forsee the length
        # of each record. So, the safest would be a copy row by row.
        # In the future, some analysis can be done in order to buffer
        # the copy process.
        nrowsinbuf = 1
        (start, stop, step) = processRangeRead(self.nrows, start, stop, step)
        # Optimized version (no conversions, no type and shape checks, etc...)
        nrowscopied = 0
        nbytes = 0
        atomsize = self.atom.atomsize()
        for start2 in xrange(start, stop, step*nrowsinbuf):
            # Save the records on disk
            stop2 = start2+step*nrowsinbuf
            if stop2 > stop:
                stop2 = stop
            nparr = self._readArray(start=start2, stop=stop2, step=step)[0]
            nobjects = nparr.shape[0]
            object._append(nparr, nobjects)
            nbytes += nobjects*atomsize
            nrowscopied +=1
        object.nrows = nrowscopied
        return (object, nbytes)

    def __repr__(self):
        """This provides more metainfo in addition to standard __str__"""

        return """%s
  atom = %r
  byteorder = %r
  nrows = %s
  flavor = %r""" % (self, self.atom, self.byteorder, self.nrows,
                    self.flavor)
