from bson import SON, DBRef

from mongoengine.base import (
    BaseDict,
    BaseList,
    EmbeddedDocumentList,
    TopLevelDocumentMetaclass,
    _DocumentRegistry,
)
from mongoengine.base.datastructures import (
    AsyncReferenceProxy,
    LazyReference,
)
from mongoengine.connection import _get_session, get_db
from mongoengine.document import Document, EmbeddedDocument
from mongoengine.fields import (
    DictField,
    ListField,
    MapField,
    ReferenceField,
)
from mongoengine.queryset import QuerySet


class DeReference:
    def __call__(self, items, max_depth=1, instance=None, name=None):
        """
        Cheaply dereferences the items to a set depth.
        Also handles the conversion of complex data types.

        :param items: The iterable (dict, list, queryset) to be dereferenced.
        :param max_depth: The maximum depth to recurse to
        :param instance: The owning instance used for tracking changes by
            :class:`~mongoengine.base.ComplexBaseField`
        :param name: The name of the field, used for tracking changes by
            :class:`~mongoengine.base.ComplexBaseField`
        :param get: A boolean determining if being called by __get__
        """
        if items is None or isinstance(items, str):
            return items

        # cheapest way to convert a queryset to a list
        # list(queryset) uses a count() query to determine length
        if isinstance(items, QuerySet):
            items = [i for i in items]

        self.max_depth = max_depth
        doc_type = None

        if instance and isinstance(
            instance, (Document, EmbeddedDocument, TopLevelDocumentMetaclass)
        ):
            doc_type = instance._fields.get(name)
            while hasattr(doc_type, "field"):
                doc_type = doc_type.field

            if isinstance(doc_type, ReferenceField):
                field = doc_type
                doc_type = doc_type.document_type
                is_list = not hasattr(items, "items")

                if is_list and all(i.__class__ == doc_type for i in items):
                    return items
                elif not is_list and all(
                    i.__class__ == doc_type for i in items.values()
                ):
                    return items
                elif not field.dbref:
                    # We must turn the ObjectIds into DBRefs

                    # Recursively dig into the sub items of a list/dict
                    # to turn the ObjectIds into DBRefs
                    def _get_items_from_list(items):
                        new_items = []
                        for v in items:
                            value = v
                            if isinstance(v, dict):
                                value = _get_items_from_dict(v)
                            elif isinstance(v, list):
                                value = _get_items_from_list(v)
                            elif not isinstance(v, (DBRef, Document)):
                                value = field.to_python(v)
                            new_items.append(value)
                        return new_items

                    def _get_items_from_dict(items):
                        new_items = {}
                        for k, v in items.items():
                            value = v
                            if isinstance(v, list):
                                value = _get_items_from_list(v)
                            elif isinstance(v, dict):
                                value = _get_items_from_dict(v)
                            elif not isinstance(v, (DBRef, Document)):
                                value = field.to_python(v)
                            new_items[k] = value
                        return new_items

                    if not hasattr(items, "items"):
                        items = _get_items_from_list(items)
                    else:
                        items = _get_items_from_dict(items)

        self.reference_map = self._find_references(items)
        self.object_map = self._fetch_objects(doc_type=doc_type)
        return self._attach_objects(items, 0, instance, name)

    def _find_references(self, items, depth=0):
        """
        Recursively finds all db references to be dereferenced

        :param items: The iterable (dict, list, queryset)
        :param depth: The current depth of recursion
        """
        reference_map = {}
        if not items or depth >= self.max_depth:
            return reference_map

        # Determine the iterator to use
        if isinstance(items, dict):
            iterator = items.values()
        else:
            iterator = items

        # Recursively find dbreferences
        depth += 1
        for item in iterator:
            if isinstance(item, (Document, EmbeddedDocument)):
                for field_name, field in item._fields.items():
                    v = item._data.get(field_name, None)
                    if isinstance(v, LazyReference):
                        # LazyReference inherits DBRef but should not be dereferenced here !
                        continue
                    elif isinstance(v, DBRef):
                        reference_map.setdefault(field.document_type, set()).add(v.id)
                    elif isinstance(v, (dict, SON)) and "_ref" in v:
                        reference_map.setdefault(
                            _DocumentRegistry.get(v["_cls"]), set()
                        ).add(v["_ref"].id)
                    elif isinstance(v, (dict, list, tuple)) and depth <= self.max_depth:
                        field_cls = getattr(
                            getattr(field, "field", None), "document_type", None
                        )
                        references = self._find_references(v, depth)
                        for key, refs in references.items():
                            if isinstance(
                                field_cls, (Document, TopLevelDocumentMetaclass)
                            ):
                                key = field_cls
                            reference_map.setdefault(key, set()).update(refs)
            elif isinstance(item, LazyReference):
                # LazyReference inherits DBRef but should not be dereferenced here !
                continue
            elif isinstance(item, DBRef):
                reference_map.setdefault(item.collection, set()).add(item.id)
            elif isinstance(item, (dict, SON)) and "_ref" in item:
                reference_map.setdefault(
                    _DocumentRegistry.get(item["_cls"]), set()
                ).add(item["_ref"].id)
            elif isinstance(item, (dict, list, tuple)) and depth - 1 <= self.max_depth:
                references = self._find_references(item, depth - 1)
                for key, refs in references.items():
                    reference_map.setdefault(key, set()).update(refs)

        return reference_map

    def _fetch_objects(self, doc_type=None):
        """Fetch all references and convert to their document objects"""
        object_map = {}
        for collection, dbrefs in self.reference_map.items():
            # we use getattr instead of hasattr because hasattr swallows any exception under python2
            # so it could hide nasty things without raising exceptions (cfr bug #1688))
            ref_document_cls_exists = getattr(collection, "objects", None) is not None

            if ref_document_cls_exists:
                col_name = collection._get_collection_name()
                refs = [
                    dbref for dbref in dbrefs if (col_name, dbref) not in object_map
                ]
                references = collection.objects.in_bulk(refs)
                for key, doc in references.items():
                    object_map[(col_name, key)] = doc
            else:  # Generic reference: use the refs data to convert to document
                if isinstance(doc_type, (ListField, DictField, MapField)):
                    continue

                refs = [
                    dbref for dbref in dbrefs if (collection, dbref) not in object_map
                ]

                if doc_type:
                    references = doc_type._get_db()[collection].find(
                        {"_id": {"$in": refs}}, session=_get_session()
                    )
                    for ref in references:
                        doc = doc_type._from_son(ref)
                        object_map[(collection, doc.id)] = doc
                else:
                    references = get_db()[collection].find(
                        {"_id": {"$in": refs}}, session=_get_session()
                    )
                    for ref in references:
                        if "_cls" in ref:
                            doc = _DocumentRegistry.get(ref["_cls"])._from_son(ref)
                        elif doc_type is None:
                            doc = _DocumentRegistry.get(
                                "".join(x.capitalize() for x in collection.split("_"))
                            )._from_son(ref)
                        else:
                            doc = doc_type._from_son(ref)
                        object_map[(collection, doc.id)] = doc
        return object_map

    def _attach_objects(self, items, depth=0, instance=None, name=None):
        """
        Recursively finds all db references to be dereferenced

        :param items: The iterable (dict, list, queryset)
        :param depth: The current depth of recursion
        :param instance: The owning instance used for tracking changes by
            :class:`~mongoengine.base.ComplexBaseField`
        :param name: The name of the field, used for tracking changes by
            :class:`~mongoengine.base.ComplexBaseField`
        """
        if not items:
            if isinstance(items, (BaseDict, BaseList)):
                return items

            if instance:
                if isinstance(items, dict):
                    return BaseDict(items, instance, name)
                else:
                    return BaseList(items, instance, name)

        if isinstance(items, (dict, SON)):
            if "_ref" in items:
                return self.object_map.get(
                    (items["_ref"].collection, items["_ref"].id), items
                )
            elif "_cls" in items:
                doc = _DocumentRegistry.get(items["_cls"])._from_son(items)
                _cls = doc._data.pop("_cls", None)
                del items["_cls"]
                doc._data = self._attach_objects(doc._data, depth, doc, None)
                if _cls is not None:
                    doc._data["_cls"] = _cls
                return doc

        if not hasattr(items, "items"):
            is_list = True
            list_type = BaseList
            if isinstance(items, EmbeddedDocumentList):
                list_type = EmbeddedDocumentList
            as_tuple = isinstance(items, tuple)
            iterator = enumerate(items)
            data = []
        else:
            is_list = False
            iterator = items.items()
            data = {}

        depth += 1
        for k, v in iterator:
            if is_list:
                data.append(v)
            else:
                data[k] = v

            if k in self.object_map and not is_list:
                data[k] = self.object_map[k]
            elif isinstance(v, (Document, EmbeddedDocument)):
                for field_name in v._fields:
                    field_value = data[k]._data.get(field_name, None)
                    if isinstance(field_value, DBRef):
                        data[k]._data[field_name] = self.object_map.get(
                            (field_value.collection, field_value.id), field_value
                        )
                    elif isinstance(field_value, (dict, SON)) and "_ref" in field_value:
                        data[k]._data[field_name] = self.object_map.get(
                            (field_value["_ref"].collection, field_value["_ref"].id),
                            field_value,
                        )
                    elif (
                        isinstance(field_value, (dict, list, tuple))
                        and depth <= self.max_depth
                    ):
                        item_name = f"{name}.{k}.{field_name}"
                        data[k]._data[field_name] = self._attach_objects(
                            field_value, depth, instance=instance, name=item_name
                        )
            elif isinstance(v, (dict, list, tuple)) and depth <= self.max_depth:
                item_name = f"{name}.{k}" if name else name
                data[k] = self._attach_objects(
                    v, depth - 1, instance=instance, name=item_name
                )
            elif isinstance(v, DBRef) and hasattr(v, "id"):
                data[k] = self.object_map.get((v.collection, v.id), v)

        if instance and name:
            if is_list:
                return tuple(data) if as_tuple else list_type(data, instance, name)
            return BaseDict(data, instance, name)
        depth += 1
        return data


class AsyncDeReference(DeReference):
    """Async version of DeReference that handles AsyncCursor objects."""

    async def __call__(self, items, max_depth=1, instance=None, name=None):
        """
        Async version of dereference that handles AsyncCursor.
        """
        if items is None or isinstance(items, str):
            return items

        # Handle async QuerySet by converting to list
        if isinstance(items, QuerySet):
            # Check if this is being called from an async context with an AsyncCursor
            if hasattr(items, "_cursor") and hasattr(
                items._cursor.__class__, "__anext__"
            ):
                # This is an async cursor, use async iteration
                items = []
                async for item in items:
                    items.append(item)
            else:
                # Regular sync iteration
                items = [i for i in items]

        self.max_depth = max_depth
        doc_type = None

        if instance and isinstance(
            instance, (Document, EmbeddedDocument, TopLevelDocumentMetaclass)
        ):
            doc_type = instance._fields.get(name)
            while hasattr(doc_type, "field"):
                doc_type = doc_type.field

            if isinstance(doc_type, ReferenceField):
                field = doc_type
                doc_type = doc_type.document_type
                is_list = not hasattr(items, "items")

                if is_list and all(i.__class__ == doc_type for i in items):
                    return items
                elif not is_list and all(
                    i.__class__ == doc_type for i in items.values()
                ):
                    return items
                elif not field.dbref:
                    # We must turn the ObjectIds into DBRefs

                    # Recursively dig into the sub items of a list/dict
                    # to turn the ObjectIds into DBRefs
                    def _get_items_from_list(items):
                        new_items = []
                        for v in items:
                            value = v
                            if isinstance(v, dict):
                                value = _get_items_from_dict(v)
                            elif isinstance(v, list):
                                value = _get_items_from_list(v)
                            elif not isinstance(v, (DBRef, Document)):
                                value = field.to_python(v)
                            new_items.append(value)
                        return new_items

                    def _get_items_from_dict(items):
                        new_items = {}
                        for k, v in items.items():
                            value = v
                            if isinstance(v, list):
                                value = _get_items_from_list(v)
                            elif isinstance(v, dict):
                                value = _get_items_from_dict(v)
                            elif not isinstance(v, (DBRef, Document)):
                                value = field.to_python(v)
                            new_items[k] = value
                        return new_items

                    if not hasattr(items, "items"):
                        items = _get_items_from_list(items)
                    else:
                        items = _get_items_from_dict(items)

        self.reference_map = self._find_references(items)
        self.object_map = await self._async_fetch_objects(doc_type=doc_type)
        return self._attach_objects(items, 0, instance, name)

    async def _async_fetch_objects(self, doc_type=None):
        """Async version of _fetch_objects that uses async queries."""
        from mongoengine.connection import (
            DEFAULT_CONNECTION_NAME,
            _get_session,
            get_async_db,
        )

        object_map = {}
        for collection, dbrefs in self.reference_map.items():
            ref_document_cls_exists = getattr(collection, "objects", None) is not None

            if ref_document_cls_exists:
                col_name = collection._get_collection_name()
                refs = [
                    dbref for dbref in dbrefs if (col_name, dbref) not in object_map
                ]
                # Use async in_bulk if available, otherwise fetch individually
                if hasattr(collection.objects, "async_in_bulk"):
                    references = await collection.objects.async_in_bulk(refs)
                else:
                    # Fallback: fetch individually
                    references = {}
                    for ref_id in refs:
                        try:
                            doc = await collection.objects.async_get(id=ref_id)
                            references[ref_id] = doc
                        except Exception:
                            pass

                for key, doc in references.items():
                    object_map[(col_name, key)] = doc
            else:  # Generic reference: use the refs data to convert to document
                if isinstance(doc_type, (ListField, DictField, MapField)):
                    continue

                refs = [
                    dbref for dbref in dbrefs if (collection, dbref) not in object_map
                ]

                if doc_type:
                    db = get_async_db(
                        doc_type._meta.get("db_alias", DEFAULT_CONNECTION_NAME)
                    )
                    cursor = db[collection].find(
                        {"_id": {"$in": refs}}, session=_get_session()
                    )
                    async for ref in cursor:
                        doc = doc_type._from_son(ref)
                        object_map[(collection, doc.id)] = doc
                else:
                    db = get_async_db(DEFAULT_CONNECTION_NAME)
                    cursor = db[collection].find(
                        {"_id": {"$in": refs}}, session=_get_session()
                    )
                    async for ref in cursor:
                        if "_cls" in ref:
                            doc = _DocumentRegistry.get(ref["_cls"])._from_son(ref)
                        elif doc_type is None:
                            doc = _DocumentRegistry.get(
                                "".join(x.capitalize() for x in collection.split("_"))
                            )._from_son(ref)
                        else:
                            doc = doc_type._from_son(ref)
                        object_map[(collection, doc.id)] = doc
        return object_map

    def _find_references(self, items, depth=0):
        """
        Override to also handle AsyncReferenceProxy objects when finding references.
        """
        reference_map = {}
        if not items or depth >= self.max_depth:
            return reference_map

        # Determine the iterator to use
        if isinstance(items, dict):
            iterator = items.values()
        else:
            iterator = items

        # Recursively find dbreferences
        depth += 1
        for item in iterator:
            if isinstance(item, (Document, EmbeddedDocument)):
                for field_name, field in item._fields.items():
                    v = item._data.get(field_name, None)

                    # Handle AsyncReferenceProxy
                    if isinstance(v, AsyncReferenceProxy):
                        # Get the actual reference from the proxy
                        actual_ref = v.instance._data.get(field_name)
                        if isinstance(actual_ref, DBRef):
                            reference_map.setdefault(field.document_type, set()).add(
                                actual_ref.id
                            )
                        elif hasattr(actual_ref, "id"):  # ObjectId
                            reference_map.setdefault(field.document_type, set()).add(
                                actual_ref
                            )
                    elif isinstance(v, LazyReference):
                        # LazyReference inherits DBRef but should not be dereferenced here !
                        continue
                    elif isinstance(v, DBRef):
                        reference_map.setdefault(field.document_type, set()).add(v.id)
                    elif isinstance(v, (dict, SON)) and "_ref" in v:
                        reference_map.setdefault(
                            _DocumentRegistry.get(v["_cls"]), set()
                        ).add(v["_ref"].id)
                    elif isinstance(v, (dict, list, tuple)) and depth <= self.max_depth:
                        field_cls = getattr(
                            getattr(field, "field", None), "document_type", None
                        )
                        references = self._find_references(v, depth)
                        for key, refs in references.items():
                            if isinstance(
                                field_cls, (Document, TopLevelDocumentMetaclass)
                            ):
                                key = field_cls
                            reference_map.setdefault(key, set()).update(refs)
            elif isinstance(item, LazyReference):
                # LazyReference inherits DBRef but should not be dereferenced here !
                continue
            elif isinstance(item, DBRef):
                reference_map.setdefault(item.collection, set()).add(item.id)
            elif isinstance(item, (dict, SON)) and "_ref" in item:
                reference_map.setdefault(
                    _DocumentRegistry.get(item["_cls"]), set()
                ).add(item["_ref"].id)
            elif isinstance(item, (dict, list, tuple)) and depth - 1 <= self.max_depth:
                references = self._find_references(item, depth - 1)
                for key, refs in references.items():
                    reference_map.setdefault(key, set()).update(refs)

        return reference_map

    def _attach_objects(self, items, depth=0, instance=None, name=None):
        """
        Override to handle AsyncReferenceProxy objects in async context.
        """
        if not items:
            if isinstance(items, (BaseDict, BaseList)):
                return items

            if instance:
                if isinstance(items, dict):
                    return BaseDict(items, instance, name)
                else:
                    return BaseList(items, instance, name)

        if isinstance(items, (dict, SON)):
            if "_ref" in items:
                return self.object_map.get(
                    (items["_ref"].collection, items["_ref"].id), items
                )
            elif "_cls" in items:
                doc = _DocumentRegistry.get(items["_cls"])._from_son(items)
                _cls = doc._data.pop("_cls", None)
                del items["_cls"]
                doc._data = self._attach_objects(doc._data, depth, doc, None)
                if _cls is not None:
                    doc._data["_cls"] = _cls
                return doc

        if not hasattr(items, "items"):
            is_list = True
            list_type = BaseList
            if isinstance(items, EmbeddedDocumentList):
                list_type = EmbeddedDocumentList
            as_tuple = isinstance(items, tuple)
            iterator = enumerate(items)
            data = []
        else:
            is_list = False
            iterator = items.items()
            data = {}

        depth += 1
        for k, v in iterator:
            if is_list:
                data.append(v)
            else:
                data[k] = v

            if k in self.object_map and not is_list:
                data[k] = self.object_map[k]
            elif isinstance(v, (Document, EmbeddedDocument)):
                # Process fields in the document
                for field_name, field in v._fields.items():
                    field_value = v._data.get(field_name, None)

                    # Handle AsyncReferenceProxy - replace with actual document if we have it
                    if isinstance(field_value, AsyncReferenceProxy):
                        # Get the actual reference from the proxy
                        actual_ref = field_value.instance._data.get(field_name)
                        if isinstance(actual_ref, DBRef):
                            col_name = actual_ref.collection
                            ref_id = actual_ref.id
                        elif hasattr(actual_ref, "id"):  # ObjectId
                            col_name = field.document_type._get_collection_name()
                            ref_id = actual_ref
                        else:
                            continue

                        if (col_name, ref_id) in self.object_map:
                            v._data[field_name] = self.object_map[(col_name, ref_id)]
                    elif isinstance(field_value, DBRef):
                        v._data[field_name] = self.object_map.get(
                            (field_value.collection, field_value.id), field_value
                        )
                    elif isinstance(field_value, (dict, SON)) and "_ref" in field_value:
                        v._data[field_name] = self.object_map.get(
                            (field_value["_ref"].collection, field_value["_ref"].id),
                            field_value,
                        )
                    elif (
                        isinstance(field_value, (dict, list, tuple))
                        and depth <= self.max_depth
                    ):
                        item_name = f"{name}.{k}.{field_name}"
                        v._data[field_name] = self._attach_objects(
                            field_value, depth, instance=instance, name=item_name
                        )

                # Update data with the modified document
                data[k] = v
            elif isinstance(v, (dict, list, tuple)) and depth <= self.max_depth:
                item_name = f"{name}.{k}" if name else name
                data[k] = self._attach_objects(
                    v, depth - 1, instance=instance, name=item_name
                )
            elif isinstance(v, DBRef) and hasattr(v, "id"):
                data[k] = self.object_map.get((v.collection, v.id), v)

        if instance and name:
            if is_list:
                return tuple(data) if as_tuple else list_type(data, instance, name)
            return BaseDict(data, instance, name)
        return data
