import re
import itertools
from copy import deepcopy
from collections import deque
from typing import TYPE_CHECKING, Optional, Iterator, Any, List, Callable, Dict
import warnings

from . import component as comp
from . import rdltypes
from .core import rdlformatcode, helpers

if TYPE_CHECKING:
    from .compiler import RDLEnvironment
    from markdown import Markdown

class Node:
    """
    The Node object is a higher-level overlay that provides a more user-friendly
    interface to query the compiled RDL object model.

    .. inheritance-diagram:: systemrdl.node
        :top-classes: ~Node

    """

    def __init__(self, inst: comp.Component, env: 'RDLEnvironment', parent: Optional['Node']):
        # Generic Node constructor.
        # Do not call directly. Use factory() static method instead
        self.env = env

        #: Reference to :class:`~systemrdl.component.Component` that instantiates this node
        self.inst = inst

        #: Reference to parent :class:`~Node`
        self.parent = parent

    def __repr__(self) -> str:
        return "<%s %s at 0x%x>" % (
            self.__class__.__qualname__,
            self.get_path(),
            id(self)
        )


    def __deepcopy__(self, memo: Dict[int, Any]) -> 'Node':
        """
        Deepcopy the node overlay.
        Members that are not part of the overlay (component tree) are not
        deepcopied.

        .. versionadded:: 1.8
        """
        copy_by_ref = ["inst", "env"]
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            if k in copy_by_ref:
                setattr(result, k, v)
            else:
                setattr(result, k, deepcopy(v, memo))
        return result


    @staticmethod
    def _factory(inst: comp.Component, env: 'RDLEnvironment', parent: Optional['Node']=None) -> 'Node':
        if isinstance(inst, comp.Field):
            return FieldNode(inst, env, parent)
        elif isinstance(inst, comp.Reg):
            return RegNode(inst, env, parent)
        elif isinstance(inst, comp.Regfile):
            return RegfileNode(inst, env, parent)
        elif isinstance(inst, comp.Addrmap):
            return AddrmapNode(inst, env, parent)
        elif isinstance(inst, comp.Mem):
            return MemNode(inst, env, parent)
        elif isinstance(inst, comp.Signal):
            return SignalNode(inst, env, parent)
        else:
            raise RuntimeError


    @classmethod
    def add_derived_property(cls, getter_function: Callable, name: Optional[str]=None) -> None:
        """
        Register a user-defined derived property

        Parameters
        ----------
        getter_function : function
            Function that fetches the result of the user-defined derived property
        name : str
            Derived property name
            If unassigned, will default to the function's name

        .. deprecated:: 1.15
            Use of this function is not recommended since it encourages the bad
            practice of modifying the SystemRDL API at runtime. Instead, it is
            recommended that any extended property derivations be done in a
            separate function outside of the Node interface.

            In future revisions, use of this function will emit a warning, and will
            eventually be removed entirely.
        """
        warnings.warn("deprecated", DeprecationWarning)

        if name is None:
            name = getter_function.__name__
        mp = property(fget=getter_function)
        setattr(cls, name, mp)


    def children(self, unroll: bool=False, skip_not_present: bool=True) -> Iterator['Node']:
        """
        Returns an iterator that provides nodes for all immediate children of
        this component.

        Parameters
        ----------
        unroll : bool
            If True, any children that are arrays are unrolled.

        skip_not_present : bool
            If True, skips children whose 'ispresent' property is set to False

        Yields
        ------
        :class:`~Node`
            All immediate children
        """
        for child_inst in self.inst.children:
            if skip_not_present:
                # Check if property ispresent == False
                if not child_inst.properties.get('ispresent', True):
                    # ispresent was explicitly set to False. Skip it
                    continue

            if unroll and isinstance(child_inst, comp.AddressableComponent) and child_inst.is_array:
                assert child_inst.array_dimensions is not None
                # Unroll the array
                range_list = [range(n) for n in child_inst.array_dimensions]
                for idxs in itertools.product(*range_list):
                    N = Node._factory(child_inst, self.env, self)
                    N.current_idx = idxs # type: ignore # pylint: disable=attribute-defined-outside-init
                    yield N
            else:
                yield Node._factory(child_inst, self.env, self)


    def descendants(self, unroll: bool=False, skip_not_present: bool=True, in_post_order: bool=False) -> Iterator['Node']:
        """
        Returns an iterator that provides nodes for all descendants of this
        component.

        Parameters
        ----------
        unroll : bool
            If True, any children that are arrays are unrolled.

        skip_not_present : bool
            If True, skips children whose 'ispresent' property is set to False

        in_post_order : bool
            If True, descendants are walked using post-order traversal
            (children first) rather than the default pre-order traversal
            (parents first).

        Yields
        ------
        :class:`~Node`
            All descendant nodes of this component
        """
        for child in self.children(unroll, skip_not_present):
            if in_post_order:
                yield from child.descendants(unroll, skip_not_present, in_post_order)

            yield child

            if not in_post_order:
                yield from child.descendants(unroll, skip_not_present, in_post_order)


    def signals(self, skip_not_present: bool=True) -> Iterator['SignalNode']:
        """
        Returns an iterator that provides nodes for all immediate signals of
        this component.

        Parameters
        ----------
        skip_not_present : bool
            If True, skips children whose 'ispresent' property is set to False

        Yields
        ------
        :class:`~SignalNode`
            All signals in this component
        """
        for child in self.children(skip_not_present=skip_not_present):
            if isinstance(child, SignalNode):
                yield child


    def fields(self, skip_not_present: bool=True) -> Iterator['FieldNode']:
        """
        Returns an iterator that provides nodes for all immediate fields of
        this component.

        Parameters
        ----------
        skip_not_present : bool
            If True, skips children whose 'ispresent' property is set to False

        Yields
        ------
        :class:`~FieldNode`
            All fields in this component
        """
        for child in self.children(skip_not_present=skip_not_present):
            if isinstance(child, FieldNode):
                yield child


    def registers(self, unroll: bool=False, skip_not_present: bool=True) -> Iterator['RegNode']:
        """
        Returns an iterator that provides nodes for all immediate registers of
        this component.

        Parameters
        ----------
        unroll : bool
            If True, any children that are arrays are unrolled.

        skip_not_present : bool
            If True, skips children whose 'ispresent' property is set to False

        Yields
        ------
        :class:`~RegNode`
            All registers in this component
        """
        for child in self.children(unroll, skip_not_present):
            if isinstance(child, RegNode):
                yield child


    @property
    def owning_addrmap(self) -> Optional['AddrmapNode']:
        """
        Returns the enclosing addrmap that owns this node.

        If this node is already an addrmap, returns itself

        If not enclosed in an addrmap, returns None

        .. versionadded:: 1.12
        """
        if isinstance(self, AddrmapNode):
            return self
        elif isinstance(self, RootNode):
            return None
        else:
            assert self.parent is not None
            return self.parent.owning_addrmap


    def get_child_by_name(self, inst_name: str) -> Optional['Node']:
        """
        Returns an immediate child :class:`~Node` whose instance name matches ``inst_name``

        Returns ``None`` if ``inst_name`` does not match

        Parameters
        ----------
        inst_name: str
            Name of immediate child to get

        Returns
        -------
        :class:`~Node` or None
            Child Node. None if not found.
        """
        child_inst = self.inst.get_child_by_name(inst_name)
        if child_inst is None:
            return None
        return Node._factory(child_inst, self.env, self)


    def find_by_path(self, path: str) -> Optional['Node']:
        """
        Finds the descendant node that is located at the relative path
        Returns ``None`` if not found
        Raises exception if path is malformed, or array index is out of range

        Parameters
        ----------
        path: str
            Path to target relative to current node

        Returns
        -------
        :class:`~Node` or None
            Descendant Node. None if not found.

        Raises
        ------
        ValueError
            If path syntax is invalid
        IndexError
            If an array index in the path is invalid
        """
        pathparts = path.split('.')
        current_node = self
        for pathpart in pathparts:
            # If parent reference, jump upwards
            if pathpart == "^":
                if current_node.parent:
                    current_node = current_node.parent
                continue

            # .. otherwise continue parsing the path
            m = re.fullmatch(r'^(\w+)((?:\[(?:\d+|0[xX][\da-fA-F]+)\])*)$', pathpart)
            if not m:
                raise ValueError("Invalid path")
            inst_name, array_suffix = m.group(1, 2)
            idx_list = [int(s, 0) for s in re.findall(r'\[(\d+|0[xX][\da-fA-F]+)\]', array_suffix)]

            current_node = current_node.get_child_by_name(inst_name)
            if current_node is None:
                return None

            if idx_list:
                if not isinstance(current_node, AddressableNode):
                    raise IndexError("Index attempted on unindexable component")
                assert isinstance(current_node.inst, comp.AddressableComponent)

                if current_node.inst.is_array:
                    # is an array
                    if len(idx_list) != len(current_node.inst.array_dimensions):
                        raise IndexError("Wrong number of array dimensions")

                    current_node.current_idx = [] # pylint: disable=attribute-defined-outside-init
                    for i, idx in enumerate(idx_list):
                        if idx >= current_node.inst.array_dimensions[i]:
                            raise IndexError("Array index out of range")
                        current_node.current_idx.append(idx)
                else:
                    raise IndexError("Index attempted on non-array component")

        return current_node


    def get_property(self, prop_name: str, **kwargs: Any)-> Any:
        """
        Gets the SystemRDL component property

        If a property was not explicitly set in the RDL source, its default
        value is derived. In some cases, a default value is implied according to
        other property values.

        Properties values that are a reference to a component instance are
        converted to a :class:`~Node` overlay object.

        Parameters
        ----------
        prop_name: str
            SystemRDL property name
        default:
            Override built-in default value of property.
            If the property was not explicitly set, return this value rather than
            the property's intrinsic default value.

        Raises
        ------
        LookupError
            If prop_name is invalid
        """

        ovr_default = False
        default = None
        if 'default' in kwargs:
            ovr_default = True
            default = kwargs.pop('default')

        # Check for stray kwargs
        if kwargs:
            raise TypeError("got an unexpected keyword argument '%s'" % list(kwargs.keys())[0])

        # If its already in the component, then safe to bypass checks
        if prop_name in self.inst.properties:
            prop_value = self.inst.properties[prop_name]

            if isinstance(prop_value, rdltypes.ComponentRef):
                # If this is a hierarchical component reference, convert it to a Node reference
                prop_value = prop_value.build_node_ref(self, self.env)
            elif isinstance(prop_value, rdltypes.PropertyReference):
                prop_value._resolve_node(self)
            elif (prop_name == "desc") and self.env.dedent_desc:
                prop_value = helpers.dedent_text(prop_value)

            return prop_value

        if ovr_default:
            # Default value is being overridden by user. Return their value
            return default

        # Otherwise, return its default value based on the property's rules
        rule = self.env.property_rules.lookup_property(prop_name)

        # Is it even a valid property or allowed for this component type?
        if rule is None:
            raise LookupError("Unknown property '%s'" % prop_name)
        if type(self.inst) not in rule.bindable_to:
            raise LookupError("Unknown property '%s'" % prop_name)

        # Return the default value as specified by the rulebook
        return rule.get_default(self)


    def list_properties(self, list_all: bool=False, include_native: bool=True, include_udp: bool=True) -> List[str]:
        """
        Lists properties associated with this node.
        By default, only lists properties that were explicitly set. If ``list_all`` is
        set to ``True`` then lists all valid properties of this component type

        Parameters
        ----------
        list_all: bool
            If true, lists all valid properties of this component type.
        include_native: bool
            If set to false, does not list native SystemRDL properties in the output.
        include_udp: bool
            If set to false, does not list user-defined properties in the output.


        .. versionchanged:: 1.12
            Added ``include_native`` and ``include_udp`` options.
        """

        if list_all:
            props = []
            if include_native:
                for k, v in self.env.property_rules.rdl_properties.items():
                    if type(self.inst) in v.bindable_to:
                        props.append(k)
            if include_udp:
                for k, v in self.env.property_rules.user_properties.items():
                    if type(self.inst) in v.bindable_to:
                        props.append(k)
            return props
        else:
            if include_native and include_udp:
                return list(self.inst.properties.keys())
            else:
                props = []
                for prop_name in self.inst.properties.keys():
                    if include_native and prop_name in self.env.property_rules.rdl_properties:
                        props.append(prop_name)
                    if include_udp and prop_name in self.env.property_rules.user_properties:
                        props.append(prop_name)
                return props


    def get_path_segment(self, array_suffix: str="[{index:d}]", empty_array_suffix: str="[]") -> str:
        """
        Gets the hierarchical path segment for just this node. This includes the
        instance name and any array suffixes.

        Parameters
        ----------
        array_suffix: str
            Override how array suffixes are represented when the index is known
        empty_array_suffix: str
            Override how array suffixes are represented when the index is not known
        """
        # pylint: disable=unused-argument
        return self.inst.inst_name


    def get_path_segments(self, array_suffix: str="[{index:d}]", empty_array_suffix: str="[]") -> List[str]:
        """
        Gets a list of path segments that represent the hierarchical path.

        .. versionadded:: 1.8
        """
        if self.parent and not isinstance(self.parent, RootNode):
            segs = self.parent.get_path_segments(array_suffix, empty_array_suffix)
            segs.append(self.get_path_segment(array_suffix, empty_array_suffix))
        elif not isinstance(self, RootNode):
            segs = [self.get_path_segment(array_suffix, empty_array_suffix)]
        else:
            segs = []
        return segs


    def get_path(self, hier_separator: str=".", array_suffix: str="[{index:d}]", empty_array_suffix: str="[]") -> str:
        """
        Generate an absolute path string to this node

        Parameters
        ----------
        hier_separator: str
            Override the hierarchy separator
        array_suffix: str
            Override how array suffixes are represented when the index is known.

            The suffix is processed using
            `string.format() <https://docs.python.org/3/library/string.html#string.Formatter.format>`_
            with the following available kwargs:

            * ``index``: The current array index
            * ``dim``: The number of elements in the array dimension

        empty_array_suffix: str
            Override how array suffixes are represented when the index is not known.

            The suffix is processed using
            `string.format() <https://docs.python.org/3/library/string.html#string.Formatter.format>`_
            with the following available kwargs:

            * ``dim``: The number of elements in the array dimension


        .. versionchanged:: 1.17
            Added ``dim`` kwarg to suffix formatting.
        """
        segs = self.get_path_segments(array_suffix, empty_array_suffix)
        return hier_separator.join(segs)


    def get_rel_path(self, ref: 'Node', uplevel: str="^", hier_separator: str=".", array_suffix: str="[{index:d}]", empty_array_suffix: str="[]") -> str:
        """
        Generate a relative path string to this node with respect to a reference node.

        A reference to a descendant node::

            foo.bar -> foo.bar.baz.abcd = "baz.abcd"

        Relative path that traverses upwards::

            foo.bar.baz -> foo.abc.def = "^.^.abc.def"

        Relative path to self results in an empty string::

            foo.bar.baz -> foo.bar.baz = ""

        Paths between array nodes with/without indexes will result in upwards paths::

            foo.array[].baz -> foo.array[0].baz = "^.^.array[0].baz"
            foo.array[0].baz -> foo.array[].baz = "^.^.array[].baz"

        Parameters
        ----------
        ref: Node
            Reference starting point node
        uplevel: str
            Override the string that denotes traversing up by one parent
        hier_separator: str
            Override the hierarchy separator
        array_suffix: str
            Override how array suffixes are represented when the index is known
        empty_array_suffix: str
            Override how array suffixes are represented when the index is not known


        .. versionadded:: 1.8
        """

        # Collect path segments using default args to ensure paths can be compared
        ref_segs = deque(ref.get_path_segments())
        self_segs = deque(self.get_path_segments())

        # collect segments as-specified by the user
        self_segs_fmt = deque(self.get_path_segments(array_suffix, empty_array_suffix))

        # 1. pop off all common segments from front of both ref_segs and self_segs
        #   also pop off self_segs_fmt
        while ref_segs and self_segs and (ref_segs[0] == self_segs[0]):
            ref_segs.popleft()
            self_segs.popleft()
            self_segs_fmt.popleft()

        # 2. length of ref_segs remaining is how many uplevels needed
        self_segs_fmt.extendleft([uplevel] * len(ref_segs))

        # 3. remaining segments in self_segs_fmt is the rest of the path
        return hier_separator.join(self_segs_fmt)


    def get_html_desc(self, markdown_inst: Optional['Markdown']=None) -> Optional[str]:
        """
        Translates the node's 'desc' property into HTML.

        Any RDLFormatCode tags used are converted to HTML.
        The text is also fed through a Markdown processor.

        The additional Markdown processing allows designers the choice to use a
        more modern lightweight markup language as an alternative to SystemRDL's
        "RDLFormatCode".

        Parameters
        ----------
        markdown_inst: ``markdown.Markdown``
            Override the class instance of the Markdown processor.
            See the `Markdown module <https://python-markdown.github.io/reference/#Markdown>`_
            for more details.

        Returns
        -------
        str or None
            HTML formatted string.
            If node does not have a description, returns ``None``


        .. versionchanged:: 1.6
            Added ``markdown_inst`` option.
        """
        desc_str = self.get_property("desc")
        if desc_str is None:
            return None
        return rdlformatcode.rdlfc_to_html(desc_str, self, md=markdown_inst)


    def get_html_name(self) -> Optional[str]:
        """
        Translates the node's 'name' property into HTML.

        Any RDLFormatCode tags used are converted to HTML.

        Returns
        -------
        str or None
            HTML formatted string.
            If node does not have an explicitly set name, returns ``None``


        .. versionadded:: 1.8
        """
        name_str = self.get_property("name", default=None)
        if name_str is None:
            return None
        return rdlformatcode.rdlfc_to_html(name_str, self, is_desc=False)


    @property
    def inst_name(self) -> str:
        """
        Name of instantiated element
        """
        return self.inst.inst_name

    @property
    def type_name(self) -> Optional[str]:
        """
        Named definition identifier.
        If declaration was anonymous, inherits the first instance's name.
        The type name of parameterized components is normalized based on the
        instance's parameter values.

        Importers may leave this as ``None``

        .. versionadded:: 1.9
        """
        return self.inst.type_name

    @property
    def orig_type_name(self) -> Optional[str]:
        """
        Named definition identifier prior to type name normalization.
        If the declaration was anonymous, this reads as None.

        .. versionadded:: 1.9
        """
        if self.inst.original_def is None:
            # Component originated from an external importer
            return None
        else:
            return self.inst.original_def.type_name

    @property
    def external(self) -> bool:
        """
        True if instance type is external. False if internal.

        .. versionadded:: 1.9
        """
        return self.inst.external


    def __eq__(self, other: object) -> bool:
        """
        Node equality checks determine whether the other node represents the
        same position in the register model's hierarchy.
        """
        if not isinstance(other, Node):
            return NotImplemented
        return self.get_path() == other.get_path()

#===============================================================================
class AddressableNode(Node):
    """
    Base-class for any kind of node that can have an address
    """

    def __init__(self, inst: comp.AddressableComponent, env: 'RDLEnvironment', parent: Optional[Node]):
        super().__init__(inst, env, parent)

        #: List of current array indexes this node is referencing where the last
        #: item in this list iterates the most frequently
        #:
        #: If None, then the current index is unknown
        self.current_idx = None # type: Optional[List[int]]


    def get_path_segment(self, array_suffix: str="[{index:d}]", empty_array_suffix: str="[]") -> str:
        # Extends get_path_segment() in order to append any array suffixes
        path_segment = super().get_path_segment(array_suffix, empty_array_suffix)

        if self.is_array:
            if self.current_idx is None:
                # Index is not known.
                for dim in self.array_dimensions:
                    path_segment += empty_array_suffix.format(dim=dim)
                return path_segment
            else:
                # Index list is known
                for idx, dim in zip(self.current_idx, self.array_dimensions):
                    path_segment += array_suffix.format(index=idx, dim=dim)
                return path_segment
        else:
            return path_segment


    def clear_lineage_index(self) -> None:
        """
        Resets this node's, as well as all parent node array indexes to
        the 'unknown index' state.

        .. versionadded:: 1.7
        """
        if self.is_array:
            self.current_idx = None

        if isinstance(self.parent, AddressableNode):
            self.parent.clear_lineage_index()


    def zero_lineage_index(self) -> None:
        """
        Resets this node's, as well as all parent node array indexes to
        zero.

        .. versionadded:: 1.7
        """
        if self.is_array:
            self.current_idx = [0] * len(self.array_dimensions)

        if isinstance(self.parent, AddressableNode):
            self.parent.zero_lineage_index()


    @property
    def raw_address_offset(self) -> int:
        """
        Raw byte address offset of the first array element node relative to
        it's parent.

        If this node is not an array, then this is equivalent to
        :attr:`address_offset`

        """
        assert isinstance(self.inst, comp.AddressableComponent)
        return self.inst.addr_offset


    @property
    def address_offset(self) -> int:
        """
        Byte address offset of this node relative to it's parent

        If this node is an array, it's index must be known

        Raises
        ------
        ValueError
            If this property is referenced on a node whose array index is not
            fully defined
        """
        if self.is_array:
            if self.current_idx is None:
                raise ValueError("Index of array element must be known to derive address")

            # Calculate the "flattened" index of a general multidimensional array
            # For example, a component array declared as:
            #   foo[S0][S1][S2]
            # and referenced as:
            #   foo[I0][I1][I2]
            # Is flattened like this:
            #   idx = I0*S1*S2 + I1*S2 + I2
            idx = 0
            for i in range(len(self.current_idx)):
                sz = 1
                for j in range(i+1, len(self.array_dimensions)):
                    sz *= self.array_dimensions[j]
                idx += sz * self.current_idx[i]

            offset = self.raw_address_offset + idx * self.array_stride

        else:
            offset = self.raw_address_offset

        return offset


    @property
    def raw_absolute_address(self) -> int:
        """
        Get the absolute byte address of this node excluding array stride of
        all parent.

        If this node, and all parents are not an array, then this is equivalent
        to :attr:`absolute_address`

        .. versionadded:: 1.7
        """
        if self.parent and not isinstance(self.parent, RootNode):
            assert isinstance(self.parent, AddressableNode)
            return self.parent.raw_absolute_address + self.raw_address_offset
        else:
            return self.raw_address_offset


    @property
    def absolute_address(self) -> int:
        """
        Get the absolute byte address of this node.

        Indexes of all arrays in the node's lineage must be known

        Raises
        ------
        ValueError
            If this property is referenced on a node whose array lineage is not
            fully defined

        """
        if self.parent and not isinstance(self.parent, RootNode):
            assert isinstance(self.parent, AddressableNode)
            return self.parent.absolute_address + self.address_offset
        else:
            return self.address_offset


    @property
    def size(self) -> int:
        """
        Determine the size (in bytes) of this node.

        If an array, returns the size of a single element
        """
        # must be overridden
        raise NotImplementedError


    @property
    def total_size(self) -> int:
        """
        Determine the size (in bytes) of this node.
        If an array, returns size of the entire array
        """
        assert isinstance(self.inst, comp.AddressableComponent)
        if self.is_array:
            # Total size of arrays is technically supposed to be:
            #   self.inst.array_stride * (self.inst.n_elements-1) + self.size
            # However this opens up a whole slew of ugly corner cases that the
            # spec designers may not have anticipated.
            # Using a simplified calculation for now until someone actually cares
            return self.array_stride * self.inst.n_elements

        else:
            return self.size


    @property
    def is_array(self) -> bool:
        """
        Indicates that this node represents an array of instances
        """
        assert isinstance(self.inst, comp.AddressableComponent)
        return self.inst.is_array


    @property
    def array_dimensions(self) -> Optional[List[int]]:
        """
        List of sizes for each array dimension.
        Last item in the list iterates the most frequently.

        If node is not an array (``is_array == False``), then this is ``None``
        """
        assert isinstance(self.inst, comp.AddressableComponent)
        return self.inst.array_dimensions


    @property
    def array_stride(self) -> Optional[int]:
        """
        Address offset between array elements.

        If node is not an array (``is_array == False``), then this is ``None``
        """
        assert isinstance(self.inst, comp.AddressableComponent)
        return self.inst.array_stride

#===============================================================================
class VectorNode(Node):
    """
    Base-class for any kind of node that is vector-like.
    """

    @property
    def width(self) -> int:
        """
        Width of vector in bits
        """
        assert isinstance(self.inst, comp.VectorComponent)
        return self.inst.width

    @property
    def msb(self) -> int:
        """
        Bit position of most significant bit
        """
        assert isinstance(self.inst, comp.VectorComponent)
        return self.inst.msb

    @property
    def lsb(self) -> int:
        """
        Bit position of least significant bit
        """
        assert isinstance(self.inst, comp.VectorComponent)
        return self.inst.lsb

    @property
    def high(self) -> int:
        """
        High index of bit range
        """
        assert isinstance(self.inst, comp.VectorComponent)
        return self.inst.high

    @property
    def low(self) -> int:
        """
        Low index of bit range
        """
        assert isinstance(self.inst, comp.VectorComponent)
        return self.inst.low


#===============================================================================
class RootNode(Node):
    @property
    def top(self) -> 'AddrmapNode':
        """
        Returns the top-level addrmap node
        """
        for child in self.children(skip_not_present=False):
            if not isinstance(child, AddrmapNode):
                continue
            return child
        raise RuntimeError

#===============================================================================
class SignalNode(VectorNode):
    pass

#===============================================================================
class FieldNode(VectorNode):

    @property
    def is_virtual(self) -> bool:
        """
        Determines if this node represents a virtual field (child of a virtual register)
        """
        assert isinstance(self.parent, RegNode)
        return self.parent.is_virtual

    @property
    def is_volatile(self) -> bool:
        """
        True if combination of field access properties result in a field that
        should be interpreted as volatile.
        (Any hardware-writable field is inherently volatile)
        """

        hw = self.get_property('hw')
        return (
            (hw in (rdltypes.AccessType.rw, rdltypes.AccessType.rw1,
                    rdltypes.AccessType.w, rdltypes.AccessType.w1))
            or self.get_property('counter')
            or (self.get_property('next') is not None)
            or self.get_property('hwset')
            or self.get_property('hwclr')
        )

    @property
    def is_sw_writable(self) -> bool:
        """
        Field is writable by software
        """
        sw = self.get_property('sw')

        return sw in (rdltypes.AccessType.rw, rdltypes.AccessType.rw1,
                        rdltypes.AccessType.w, rdltypes.AccessType.w1)

    @property
    def is_sw_readable(self) -> bool:
        """
        Field is readable by software
        """
        sw = self.get_property('sw')

        return sw in (rdltypes.AccessType.rw, rdltypes.AccessType.rw1,
                        rdltypes.AccessType.r)

    @property
    def is_hw_writable(self) -> bool:
        """
        Field is writable by hardware
        """
        hw = self.get_property('hw')

        return hw in (rdltypes.AccessType.rw, rdltypes.AccessType.rw1,
                        rdltypes.AccessType.w, rdltypes.AccessType.w1)

    @property
    def is_hw_readable(self) -> bool:
        """
        Field is readable by hardware
        """
        hw = self.get_property('hw')

        return hw in (rdltypes.AccessType.rw, rdltypes.AccessType.rw1,
                        rdltypes.AccessType.r)

    @property
    def implements_storage(self) -> bool:
        """
        True if combination of field access properties imply that the field
        implements a storage element.
        """

        # 9.4.1, Table 12
        sw = self.get_property('sw')
        hw = self.get_property('hw')
        if sw in (rdltypes.AccessType.rw, rdltypes.AccessType.rw1):
            # Software can read and write, implying a storage element
            return True
        if hw == rdltypes.AccessType.rw:
            # Hardware can read and write, implying a storage element
            return True
        if (sw in (rdltypes.AccessType.w, rdltypes.AccessType.w1)) and (hw == rdltypes.AccessType.r):
            # Write-only register visible to hardware is stored
            return True


        onread = self.get_property('onread')
        if onread is not None:
            # 9.6.1-c: Onread side-effects imply storage regardless of whether
            # or not the field is writable by sw
            return True


        if self.get_property('hwset') or self.get_property('hwclr'):
            # Not in spec, but these imply that a storage element exists
            return True

        return False

#===============================================================================
class RegNode(AddressableNode):

    @property
    def size(self) -> int:
        return self.get_property('regwidth') // 8

    @property
    def is_virtual(self) -> bool:
        """
        True if this node represents a virtual register.
        (child of a mem component)
        """
        # since mem components can only contain reg instances, a reg can only be
        # virtual if its direct parent is of type mem
        return isinstance(self.parent, MemNode)

    @property
    def has_sw_writable(self) -> bool:
        """
        Register contains one or more present fields writable by software
        """
        for field in self.fields():
            if field.is_sw_writable:
                return True
        return False

    @property
    def has_sw_readable(self) -> bool:
        """
        Register contains one or more present fields readable by software
        """
        for field in self.fields():
            if field.is_sw_readable:
                return True
        return False

#===============================================================================
class RegfileNode(AddressableNode):

    @property
    def size(self) -> int:
        return get_group_node_size(self)

#===============================================================================
class AddrmapNode(AddressableNode):

    @property
    def size(self) -> int:
        return get_group_node_size(self)

#===============================================================================
class MemNode(AddressableNode):

    @property
    def size(self) -> int:
        memwidth = max(self.get_property('memwidth'), 8)
        entry_size = helpers.roundup_pow2(memwidth) // 8
        num_entries = self.get_property('mementries')
        return entry_size * num_entries

#===============================================================================
def get_group_node_size(node: AddressableNode) -> int:
    """
    Shared getter for AddrmapNode and RegfileNode's "size" property
    """
    # After structural placement, children are sorted
    if(not node.inst.children
        or (not isinstance(node.inst.children[-1], comp.AddressableComponent))
    ):
        # No addressable child exists.
        return 0

    # Current node's size is based on last child
    last_child_node = Node._factory(node.inst.children[-1], node.env, node)
    assert isinstance(last_child_node, AddressableNode)
    return last_child_node.raw_address_offset + last_child_node.total_size
