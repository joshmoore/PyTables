"""
Base class for PyTables nodes
=============================

:Author:   Ivan Vilata i Balaguer
:Contact:  ivilata@carabos.com
:Created:  2005-02-11
:License:  BSD
:Revision: $Id$


Classes:

`Node`
    Abstract base class for all PyTables nodes.

Misc variables:

`__docformat__`
    The format of documentation strings in this module.
`__version__`
    Repository version of this file.
"""


import warnings

from tables.constants import MAX_TREE_DEPTH
from tables.registry import classNameDict, classIdDict
from tables.exceptions import \
     ClosedNodeError, NodeError, UndoRedoWarning, PerformanceWarning
from tables.utils import joinPath, splitPath, isVisiblePath
from tables.undoredo import moveToShadow
from tables.AttributeSet import AttributeSet



__docformat__ = 'reStructuredText'
"""The format of documentation strings in this module."""

__version__ = '$Revision$'
"""Repository version of this file."""



class MetaNode(type):

    """
    Node metaclass.

    This metaclass ensures that their instance classes get registered
    into several dictionaries (namely the `tables.utils.classNameDict`
    class name dictionary and the `tables.utils.classIdDict` class
    identifier dictionary).
    """

    def __init__(class_, name, bases, dict_):
        super(MetaNode, class_).__init__(name, bases, dict_)

        # Always register into class name dictionary.
        classNameDict[class_.__name__] = class_

        # Register into class identifier dictionary only if the class
        # has an identifier and it is different from its parents'.
        cid = getattr(class_, '_c_classId', None)
        if cid is not None:
            for base in bases:
                pcid = getattr(base, '_c_classId', None)
                if pcid == cid:
                    break
            else:
                classIdDict[cid] = class_



class Node(object):

    """
    Abstract base class for all PyTables nodes.

    This is the base class for *all* nodes in a PyTables hierarchy.
    It is an abstract class, i.e. it may not be directly instantiated;
    however, every node in the hierarchy is an instance of this class.

    A PyTables node is always hosted in a PyTables *file*, under a
    *parent group*, at a certain *depth* in the node hierarchy.  A node
    knows its own *name* in the parent group and its own *path name* in
    the file.  When using a translation map (see the `File` class), its
    *HDF5 name* might differ from its PyTables name.

    All the previous information is location-dependent, i.e. it may
    change when moving or renaming a node in the hierarchy.  A node also
    has location-independent information, such as its *HDF5 object
    identifier* and its *attribute set*.

    This class gathers the operations and attributes (both
    location-dependent and independent) which are common to all PyTables
    nodes, whatever their type is.  Nonetheless, due to natural naming
    restrictions, the names of all of these members start with a
    reserved prefix (see the `Group` class).

    Sub-classes with no children (i.e. leaf nodes) may define new
    methods, attributes and properties to avoid natural naming
    restrictions.  For instance, ``_v_attrs`` may be shortened to
    ``attrs`` and ``_f_rename`` to ``rename``.  However, the original
    methods and attributes should still be available.

    Instance variables (location dependent):

    _v_file
        The hosting `File` instance.
    _v_parent
        The parent `Group` instance.
    _v_depth
        The depth of this node in the tree (an non-negative integer
        value).
    _v_name
        The name of this node in its parent group (a string).
    _v_hdf5name
        The name of this node in the hosting HDF5 file (a string).
    _v_pathname
        The path of this node in the tree (a string).
    _v_rootgroup
        The root group instance.  This is deprecated; please use
        ``node._v_file.root``.

    Instance variables (location independent):

    _v_objectID
        The identifier of this node in the hosting HDF5 file.
    _v_attrs
        The associated `AttributeSet` instance.

    Instance variables (attribute shorthands):

    _v_title
        A description of this node.  A shorthand for the ``TITLE``
        attribute.

    Public methods (hierarchy manipulation):

    _f_close()
        Close this node in the tree.
    _f_remove([recursive])
        Remove this node from the hierarchy.
    _f_rename(newname)
        Rename this node in place.
    _f_move([newparent][, newname][, overwrite])
        Move or rename this node.
    _f_copy([newparent][, newname][, overwrite][, recursive][, **kwargs])
        Copy this node and return the new one.
    _f_isVisible()
        Is this node visible?

    Public methods (attribute handling):

    _f_getAttr(name)
        Get a PyTables attribute from this node.
    _f_setAttr(name, value)
        Set a PyTables attribute for this node.
    _f_delAttr(name)
        Delete a PyTables attribute from this node.
    """

    # This makes this class and all derived subclasses be handled by MetaNode.
    __metaclass__ = MetaNode


    # <undo-redo support>
    _c_canUndoCreate = False  # Can creation/copying be undone and redone?
    _c_canUndoRemove = False  # Can removal be undone and redone?
    _c_canUndoMove   = False  # Can movement/renaming be undone and redone?
    # </undo-redo support>


    # <properties>

    # `_v_parent` is accessed via its file to avoid upwards references.
    def _g_getparent(self):
        (parentPath, nodeName) = splitPath(self._v_pathname)
        return self._v_file._getNode(parentPath)

    _v_parent = property(
        _g_getparent, None, None, "The parent `Group` instance.")


    # '_v_rootgroup' is deprecated in favour of 'node._v_file.root'.
    def _g_getrootgroup(self):
        warnings.warn(DeprecationWarning("""\
``node._v_rootgroup`` is deprecated; please use ``node._v_file.root``"""),
                      stacklevel=2)
        return self._v_file.root

    _v_rootgroup = property(
        _g_getrootgroup, None, None, "The root group instance.")


    # '_v_attrs' is defined as a lazy read-only attribute.
    # This saves 0.7s/3.8s.
    def _g_getattrs(self):
        mydict = self.__dict__
        if '_v_attrs' in mydict:
            return mydict['_v_attrs']
        else:
            mydict['_v_attrs'] = attrs = AttributeSet(self)
            return attrs

    _v_attrs = property(_g_getattrs, None, None,
                        "The associated `AttributeSet` instance.")


    # '_v_title' is a direct read-write shorthand for the 'TITLE' attribute
    # with the empty string as a default value.
    def _g_gettitle (self):
        if hasattr(self._v_attrs, 'TITLE'):
            return self._v_attrs.TITLE
        else:
            return ''

    def _g_settitle (self, title):
        self._v_attrs.TITLE = title

    _v_title = property(_g_gettitle, _g_settitle, None,
                        "A description of this node.")

    # </properties>


    def __init__(self, parentNode, name, log=True):
        # Remember to assign these values in the root group constructor
        # if it does not use this method implementation!

        self._v_file = None
        """The hosting `File` instance."""
        self._v_pathname = None
        """The path of this node in the tree (a string)."""
        self._v_name = None
        """The name of this node in its parent group (a string)."""
        self._v_hdf5name = None
        """The name of this node in the hosting HDF5 file (a string)."""
        self._v_depth = None
        """The depth of this node in the tree (an non-negative integer value)."""
        self._v__deleting = False
        """Is the node being deleted?"""

        self._v_objectID = None
        """The identifier of this node in the hosting HDF5 file."""

        validate = new = self._v_new  # set by subclass constructor

        # Is the parent node a group?  Is it open?
        self._g_checkGroup(parentNode)
        parentNode._g_checkOpen()
        file_ = parentNode._v_file

        # Will the file be able to host a new node?
        if new:
            file_._checkWritable()

        # Find out the matching HDF5 name.
        ptname = name  # always the provided one
        h5name = file_._h5NameFromPTName(ptname)

        # Will creation be logged?
        undoEnabled = file_.isUndoEnabled()
        canUndoCreate = self._c_canUndoCreate
        if undoEnabled and not canUndoCreate:
            warnings.warn(
                "creation can not be undone nor redone for this node",
                UndoRedoWarning)

        # Bind to the parent node and set location-dependent information.
        if new:
            # Only new nodes need to be referenced.
            # Opened nodes are already known by their parent group.
            parentNode._g_refNode(self, ptname, validate)
        self._g_setLocation(parentNode, ptname, h5name)

        try:
            # hdf5Extension operations:
            #   Update node attributes.
            self._g_new(parentNode, h5name, init=True)
            #   Create or open the node and get its object ID.
            if new:
                self._v_objectID = self._g_create()
            else:
                self._v_objectID = self._g_open()

            # This allows extra operations after creating the node.
            self._g_postInitHook()
        except:
            # If anything happens, the node must be closed
            # to undo every possible registration made so far.
            # We do *not* rely on ``__del__()`` doing it later,
            # since it might never be called anyway.
            self._f_close()
            raise

        # Finally, log creation of the node.
        # This is made after the ``try`` because the node *has* been created!
        if new and log and undoEnabled and canUndoCreate:
            file_._log('CREATE', self._v_pathname)


    def __del__(self):
        # Closed `Node` instances can not be killed and revived.
        # Instead, accessing a closed and deleted (from memory, not
        # disk) one yields a *new*, open `Node` instance.  This is
        # because of two reasons:
        #
        # 1. Predictability.  After closing a `Node` and deleting it,
        #    only one thing can happen when accessing it again: a new,
        #    open `Node` instance is returned.  If closed nodes could be
        #    revived, one could get either a closed or an open `Node`.
        #
        # 2. Ease of use.  If the user wanted to access a closed node
        #    again, the only condition would be that no references to
        #    the `Node` instance were left.  If closed nodes could be
        #    revived, the user would also need to force the closed
        #    `Node` out of memory, which is not a trivial task.
        #
        if not self._f_isOpen():
            return

        # If we get here, the `Node` is still open.
        file_ = self._v_file
        if self._v_pathname in file_._aliveNodes:
            # If the node is alive, kill it (to save it).
            file_._killNode(self)
        else:
            # The node is already dead and there are no references to it,
            # so follow the usual deletion procedure.
            # This means closing the (still open) node.
            # `self._v__deleting` is asserted so that the node
            # does not try to unreference itself again from the file.
            self._v__deleting = True
            self._f_close()


    def _g_preKillHook(self):
        """Code to be called before killing the node."""
        pass


    def _g_postReviveHook(self):
        """Code to be called after reviving the node."""
        pass


    def _g_create(self):
        """Create a new HDF5 node and return its object identifier."""
        raise NotImplementedError


    def _g_open(self):
        """Open an existing HDF5 node and return its object identifier."""
        raise NotImplementedError


    def _f_isOpen(self):
        """Is this node open?"""

        if not '_v_file' in self.__dict__:
            return False
        # When the construction of a node is aborted because of an exception,
        # the ``_v_file`` attribute might exist but be set to `None`,
        # so the node is still considered closed.
        return self._v_file is not None


    def _g_checkOpen(self):
        """
        Check that the node is open.

        If the node is closed, a `ClosedNodeError` is raised.
        """

        if not self._f_isOpen():
            raise ClosedNodeError("the node object is closed")
        assert self._v_file.isopen, "found an open node in a closed file"


    def _g_setLocation(self, parentNode, ptname, h5name=None):
        """
        Set location-dependent attributes.

        Sets the location-dependent attributes of this node to reflect
        that it is placed under the specified `parentNode`, with the
        specified PyTables and HDF5 names (`ptname` and `h5name`,
        respectively).  If the HDF5 name is ``None``, it is found using
        the translation map from the parent's file.

        This also triggers the insertion of file references to this
        node.  If the maximum recommended node depth is exceeded, a
        `PerformanceWarning` is issued.
        """

        file_ = parentNode._v_file
        parentDepth = parentNode._v_depth

        self._v_file = file_
        self._v_pathname = joinPath(parentNode._v_pathname, ptname)

        if h5name is None:
            h5name = file_._h5NameFromPTName(ptname)
        self._v_name = ptname
        self._v_hdf5name = h5name

        self._v_depth = parentDepth + 1

        # Check if the node is too deep in the tree.
        if parentDepth >= MAX_TREE_DEPTH:
            warnings.warn("""\
node ``%s`` is exceeding the recommended maximum depth (%d);\
be ready to see PyTables asking for *lots* of memory and possibly slow I/O"""
                          % (self._v_pathname, MAX_TREE_DEPTH),
                          PerformanceWarning)

        file_._refNode(self, self._v_pathname)


    def _g_updateLocation(self, newParentPath):
        """
        Update location-dependent attributes.

        Updates location data when an ancestor node has changed its
        location in the hierarchy to `newParentPath`.  In fact, this
        method is expected to be called by an ancestor of this node.

        This also triggers the update of file references to this node.
        If the maximum recommended node depth is exceeded, a
        `PerformanceWarning` is issued.  This warning is assured to be
        unique.
        """

        oldPath = self._v_pathname
        newPath = joinPath(newParentPath, self._v_name)
        parentDepth = newParentPath.count('/')

        self._v_pathname = newPath
        self._v_depth = parentDepth + 1

        # Check if the node is too deep in the tree.
        if parentDepth >= MAX_TREE_DEPTH:
            warnings.warn("""\
moved descendent node is exceeding the recommended maximum depth (%d);\
be ready to see PyTables asking for *lots* of memory and possibly slow I/O"""
                          % (MAX_TREE_DEPTH,), PerformanceWarning)

        file_ = self._v_file
        file_._unrefNode(oldPath)
        file_._refNode(self, newPath)

        # Tell dependent objects about the new location of this node.
        self._g_updateDependent()


    def _g_delLocation(self):
        """
        Clear location-dependent attributes.

        This also triggers the removal of file references to this node.
        """

        file_ = self._v_file
        pathname = self._v_pathname

        self._v_file = None
        self._v_pathname = None
        self._v_name = None
        self._v_hdf5name = None
        self._v_depth = None

        # If the node object is being deleted,
        # it has already been unreferenced from the file.
        if not self._v__deleting:
            file_._unrefNode(pathname)


    def _g_postInitHook(self):
        """Code to be run after node creation and before creation logging."""
        pass


    def _g_updateDependent(self):
        """
        Update dependent objects after a location change.

        All dependent objects (but not nodes!) referencing this node
        must be updated here.
        """
        if '_v_attrs' in self.__dict__:
            self._v_attrs._g_updateNodeLocation(self)


    def _f_close(self):
        """
        Close this node in the tree.

        This releases all resources held by the node, so it should not
        be used again.  On nodes with data, it may be flushed to disk.

        The closing operation is *not* recursive, i.e. closing a group
        does not close its children.
        """

        # After calling ``_f_close()``, two conditions are met:
        #
        #   1. The node object is detached from the tree.
        #   2. *Every* attribute of the node is removed.
        #
        # Thus, cleanup operations used in ``_f_close()`` in sub-classes
        # must be run *before* calling the method in the superclass.

        if not self._f_isOpen():
            return  # the node is already closed

        myDict = self.__dict__

        # Close the associated `AttributeSet`
        # only if it has already been placed in the object's dictionary.
        if '_v_attrs' in myDict:
            self._v_attrs._f_close()

        # Detach the node from the tree if necessary.
        self._g_delLocation()

        # Finally, clear all remaining attributes from the object.
        myDict.clear()


    def _g_remove(self, recursive):
        """
        Remove this node from the hierarchy.

        If the node has children, recursive removal must be stated by
        giving `recursive` a true value; otherwise, a `NodeError` will
        be raised.

        It does not log the change.
        """

        # Remove the node from the PyTables hierarchy.
        self._v_parent._g_unrefNode(self._v_name)
        # Close the node itself.
        self._f_close()
        # hdf5Extension operations:
        #   Remove the node from the HDF5 hierarchy.
        self._g_delete()


    def _f_remove(self, recursive=False):
        """
        Remove this node from the hierarchy.

        If the node has children, recursive removal must be stated by
        giving `recursive` a true value, or a `NodeError` will be
        raised.
        """

        self._g_checkOpen()
        file_ = self._v_file
        file_._checkWritable()

        if file_.isUndoEnabled():
            if self._c_canUndoMove:
                oldPathname = self._v_pathname
                # Log *before* moving to use the right shadow name.
                file_._log('REMOVE', oldPathname)
                moveToShadow(file_, oldPathname)
            else:
                warnings.warn(
                    "removal can not be undone nor redone for this node",
                    UndoRedoWarning)
                self._g_remove(recursive)
        else:
            self._g_remove(recursive)


    def _g_move(self, newParent, newName):
        """
        Move this node in the hierarchy.

        Moves the node into the given `newParent`, with the given
        `newName`.

        It does not log the change.
        """

        oldParent = self._v_parent
        oldName = self._v_name
        oldPathname = self._v_pathname  # to move the HDF5 node

        # Try to insert the node into the new parent.
        newParent._g_refNode(self, newName)
        # Remove the node from the new parent.
        oldParent._g_unrefNode(oldName)

        # Remove location information for this node.
        self._g_delLocation()
        # Set new location information for this node.
        self._g_setLocation(newParent, newName)

        # hdf5Extension operations:
        #   Update node attributes.
        self._g_new(newParent, self._v_hdf5name, init=False)
        #   Move the node.
        #self._v_parent._g_moveNode(oldPathname, self._v_pathname)
        self._v_parent._g_moveNode(oldParent._v_objectID, oldName,
                                   newParent._v_objectID, newName,
                                   oldPathname, self._v_pathname)

        # Tell dependent objects about the new location of this node.
        self._g_updateDependent()


    def _f_rename(self, newname):
        """
        Rename this node in place.

        Changes the name of a node to `newname` (a string).
        """
        self._f_move(newname = newname)


    def _f_move(self, newparent=None, newname=None, overwrite=False):
        """
        Move or rename this node.

        Moves a node into a new parent group, or changes the name of the
        node.  `newparent` can be a `Group` object or a pathname in
        string form.  If it is not specified or ``None` , the current
        parent group is chosen as the new parent.  `newname` must be a
        string with a new name.  If it is not specified or ``None``, the
        current name is chosen as the new name.

        Moving a node across databases is not allowed, nor it is moving
        a node *into* itself.  These result in a `NodeError`.  However,
        moving a node *over* itself is allowed and simply does nothing.
        Moving over another existing node is similarly not allowed,
        unless the optional `overwrite` argument is true, in which case
        that node is recursively removed before moving.

        Usually, only the first argument will be used, effectively
        moving the node to a new location without changing its name.
        Using only the second argument is equivalent to renaming the
        node in place.
        """

        self._g_checkOpen()
        file_ = self._v_file
        oldParent = self._v_parent
        oldName = self._v_name

        # Set default arguments.
        if newparent is None and newname is None:
            raise NodeError("""\
you should specify at least a ``newparent`` or a ``newname`` parameter""")
        if newparent is None:
            newparent = oldParent
        if newname is None:
            newname = oldName

        # Validity checks on arguments.
        newparent = file_.getNode(newparent)  # Does the new parent exist?
        self._g_checkGroup(newparent)  # Is it a group?

        # The movement always fails if the hosting file can not be modified.
        file_._checkWritable()

        if newparent._v_file is not file_:  # Is it in the same file?
            raise NodeError("""\
nodes can not be moved across databases; please make a copy of the node""")

        # Moving over itself?
        if (newparent is oldParent) and (newname == oldName):
            # This is equivalent to renaming the node to its current name,
            # and it does not change the referenced object,
            # so it is an allowed no-op.
            return

        self._g_checkNotContains(newparent)  # Moving into itself?
        self._g_maybeRemove(  # Moving over an existing node?
            newparent, newname, overwrite)

        undoEnabled = file_.isUndoEnabled()
        canUndoMove = self._c_canUndoMove
        if undoEnabled and not canUndoMove:
            warnings.warn(
                "movement can not be undone nor redone for this node",
                UndoRedoWarning)

        # Move the node.
        oldPathname = self._v_pathname
        self._g_move(newparent, newname)
        newPathname = self._v_pathname

        # Log the change.
        if undoEnabled and canUndoMove:
            file_._log('MOVE', oldPathname, newPathname)


    def _g_copy(self, newParent, newName, recursive, log, **kwargs):
        """
        Copy this node and return the new one.

        Creates and returns a copy of the node in the given `newParent`,
        with the given `newName`.  If `recursive` copy is stated, all
        descendents are copied as well.  Additional keyword argumens may
        affect the way that the copy is made.  Unknown arguments must be
        ignored.  On recursive copies, all keyword arguments must be
        passed on to the children invocation of this method.

        If `log` is true, the change is logged.
        """
        raise NotImplementedError


    def _f_copy(self, newparent=None, newname=None,
                overwrite=False, recursive=False, **kwargs):
        """
        Copy this node and return the new one.

        Creates and returns a copy of the node, maybe in a different
        place in the hierarchy.  `newparent` can be a `Group` object or
        a pathname in string form.  If it is not specified or ``None``,
        the current parent group is chosen as the new parent.  `newname`
        must be a string with a new name.  If it is not specified or
        ``None``, the current name is chosen as the new name.  If
        `recursive` copy is stated, all descendents are copied as well.

        Copying a node across databases is supported but can not be
        undone.  Copying a node over itself is not allowed, nor it is
        recursively copying a node into itself.  These result in a
        `NodeError`.  Copying over another existing node is similarly
        not allowed, unless the optional `overwrite` argument is true,
        in which case that node is recursively removed before copying.

        Additional keyword arguments may be passed to customize the
        copying process.  For instance, title and filters may be
        changed, user attributes may be or may not be copied, data may
        be subsampled, stats may be collected, etc.  See the
        documentation for the particular node type.

        Using only the first argument is equivalent to copying the node
        to a new location without changing its name.  Using only the
        second argument is equivalent to making a copy of the node in
        the same group.
        """

        self._g_checkOpen()
        srcFile = self._v_file
        srcParent = self._v_parent
        srcName = self._v_name

        dstParent = newparent
        dstName = newname

        # Set default arguments.
        if dstParent is None and dstName is None:
            raise NodeError("""\
you should specify at least a ``newparent`` or a ``newname`` parameter""")
        if dstParent is None:
            dstParent = srcParent
        if dstName is None:
            dstName = srcName

        # Validity checks on arguments.
        # If ``dstParent`` is a path, it *must* be in the source file!
        dstParent = srcFile.getNode(dstParent)  # Does the new parent exist?
        self._g_checkGroup(dstParent)  # Is it a group?

        dolog = True  # Is it in the same file?
        if dstParent._v_file is not srcFile and srcFile.isUndoEnabled():
            warnings.warn("""\
copying across databases can not be undone nor redone from this database""",
                          UndoRedoWarning)
            dolog = False

        # Copying over itself?
        if (dstParent is srcParent) and (dstName == srcName):
            raise NodeError(
                "source and destination nodes are the same node: ``%s``"
                % (self._v_pathname,))

        if recursive:
            self._g_checkNotContains(dstParent)  # Copying into itself?
        self._g_maybeRemove(  # Copying over an existing node?
            dstParent, dstName, overwrite)

        # Copy the node.
        # The constructor of the new node takes care of logging.
        return self._g_copy(dstParent, dstName, recursive, True, **kwargs)


    def _f_isVisible(self):
        """Is this node visible?"""
        self._g_checkOpen()
        return isVisiblePath(self._v_pathname)


    def _g_checkGroup(self, node):
        # Node must be defined in order to define a Group.
        # However, we need to know Group here.
        # Using classNameDict avoids a circular import.
        if not isinstance(node, classNameDict['Node']):
            raise TypeError("new parent is not a node: %r" % (node,))
        if not isinstance(node, classNameDict['Group']):
            raise TypeError("new parent node ``%s`` is not a group"
                            % node._v_pathname)


    def _g_checkNotContains(self, node):
        # The not-a-TARDIS test. ;)
        if node is self or node._g_isDescendentOf(self):
            raise NodeError(
                "can not move or recursively copy node ``%s`` into itself"
                % (self._v_pathname,))


    def _g_maybeRemove(self, parent, name, overwrite):
        if name in parent:
            if not overwrite:
                raise NodeError("""\
destination group ``%s`` already has a node named ``%s``; \
you may want to use the ``overwrite`` argument""" % (parent._v_pathname, name))
            parent._f_getChild(name)._f_remove(True)


    def _g_isDescendentOf(self, group):
        # The nodes are in different files.
        if self._v_file is not group._v_file:
            return False

        # This check avoids walking up the tree.
        prefix = group._v_pathname + '/'
        if prefix == '//':
            return True  # all nodes descend from the root group
        return self._v_pathname.startswith(prefix)


    def _g_checkName(self, name):
        """
        Check validity of name for this particular kind of node.

        This is invoked once the standard HDF5 and natural naming checks
        have successfully passed.
        """

        if name.startswith('_i_'):
            # This is reserved for table index groups.
            raise ValueError(
                "node name starts with reserved prefix ``_i_``: %s" % name)


    # <attribute handling>

    def _f_getAttr(self, name):
        """
        Get a PyTables attribute from this node.

        If the named attribute does not exist, an `AttributeError` is
        raised.
        """
        return getattr(self._v_attrs, name)

    def _f_setAttr(self, name, value):
        """
        Set a PyTables attribute for this node.

        If the node already has a large number of attributes, a
        `PerformanceWarning` is issued.
        """
        setattr(self._v_attrs, name, value)

    def _f_delAttr(self, name):
        """
        Delete a PyTables attribute from this node.

        If the named attribute does not exist, an `AttributeError` is
        raised.
        """
        delattr(self._v_attrs, name)

    # </attribute handling>



## Local Variables:
## mode: python
## py-indent-offset: 4
## tab-width: 4
## fill-column: 72
## End: