package memorystore

import "errors"

// storeMetadataByPath takes a value map, a key path, and metadata as input arguments.
// It stores the metadata in the nested map structure referenced by the key path.
// If the key path is empty or contains only an empty string, the function merges
// the metadata into the value map. If a key in the path does not exist or is nil,
// it creates a new map at that key. The function returns an error if metadata is not
// of type map[string]interface{}.
func storeMetadataByPath(
	value map[string]interface{},
	keyPath []string,
	metadata interface{},
) error {
	length := len(keyPath)
	if length == 0 || (length == 1 && keyPath[0] == "") {
		metadataMap, ok := metadata.(map[string]interface{})
		if !ok {
			return errors.New("metadata must be of type map[string]interface{}")
		}
		for k, v := range metadataMap {
			value[k] = v
		}
		return nil
	}

	for idx, key := range keyPath {
		isLastKey := idx == length-1

		if existingValue, ok := value[key]; ok && isLastKey {
			existingMap, existingOk := existingValue.(map[string]interface{})
			metadataMap, metadataOk := metadata.(map[string]interface{})
			if existingOk && metadataOk {
				for k, v := range metadataMap {
					existingMap[k] = v
				}
				return nil
			}
		}

		if isLastKey {
			value[key] = metadata
		} else {
			childValue, ok := value[key]
			if !ok || childValue == nil {
				childValue = make(map[string]interface{})
				value[key] = childValue
			}
			value = childValue.(map[string]interface{})
		}
	}

	return nil
}
