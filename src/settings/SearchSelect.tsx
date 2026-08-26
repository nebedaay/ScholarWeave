import React from 'react';

export interface SelectOption {
  value: string;
  label: string;
}

interface SearchSelectProps {
  placeholder: string;
  defaultValue?: SelectOption;
  search: (query: string) => SelectOption[];
  onChange: (selection: SelectOption | null) => void;
  isClearable?: boolean;
}

/**
 * Minimal searchable-select component replacing react-select/AsyncSelect.
 * Uses Preact (aliased as React) with Obsidian CSS variables for theming.
 */
export function SearchSelect({
  placeholder,
  defaultValue,
  search,
  onChange,
  isClearable,
}: SearchSelectProps) {
  const [inputVal, setInputVal] = React.useState(defaultValue?.label ?? '');
  const [options, setOptions] = React.useState<SelectOption[]>([]);
  const [isOpen, setIsOpen] = React.useState(false);
  const [activeIndex, setActiveIndex] = React.useState(-1);
  const [selected, setSelected] = React.useState<SelectOption | null>(
    defaultValue ?? null
  );
  const dbRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const menuId = React.useId();

  const openOptions = (results: SelectOption[]) => {
    setOptions(results);
    setIsOpen(results.length > 0);
    setActiveIndex(results.length > 0 ? 0 : -1);
  };

  const handleInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setInputVal(val);
    if (dbRef.current) clearTimeout(dbRef.current);
    if (val.length >= 1) {
      dbRef.current = setTimeout(() => {
        const results = search(val).slice(0, 20);
        openOptions(results);
      }, 150);
    } else {
      openOptions([]);
    }
  };

  const handleSelect = (opt: SelectOption) => {
    setSelected(opt);
    setInputVal(opt.label);
    setOptions([]);
    setIsOpen(false);
    setActiveIndex(-1);
    onChange(opt);
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelected(null);
    setInputVal('');
    setOptions([]);
    setIsOpen(false);
    setActiveIndex(-1);
    onChange(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!options.length) return;
      setIsOpen(true);
      setActiveIndex((idx) => {
        const next =
          e.key === 'ArrowDown'
            ? (idx + 1 + options.length) % options.length
            : (idx - 1 + options.length) % options.length;
        return next;
      });
    } else if (e.key === 'Enter') {
      if (isOpen && activeIndex >= 0 && options[activeIndex]) {
        e.preventDefault();
        handleSelect(options[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false);
      setActiveIndex(-1);
    }
  };

  return (
    <div className="lc-search-select">
      <div className="lc-search-select__control">
        <input
          type="text"
          className="lc-search-select__input"
          value={inputVal}
          placeholder={placeholder}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (options.length) {
              setIsOpen(true);
              setActiveIndex((idx) => (idx >= 0 ? idx : 0));
            }
          }}
          onBlur={() => {
            // Delay close so onMouseDown on an option fires first
            setTimeout(() => {
              setIsOpen(false);
              setActiveIndex(-1);
            }, 200);
          }}
          role="combobox"
          aria-expanded={isOpen}
          aria-controls={menuId}
          aria-autocomplete="list"
          aria-activedescendant={
            isOpen && activeIndex >= 0
              ? `${menuId}-option-${activeIndex}`
              : undefined
          }
        />
        {isClearable && selected && (
          <button
            className="lc-search-select__clear clickable-icon"
            onMouseDown={handleClear}
            aria-label="Clear"
          >
            ×
          </button>
        )}
      </div>
      {isOpen && (
        <div id={menuId} className="lc-search-select__menu" role="listbox">
          {options.map((opt, index) => (
            <div
              key={opt.value}
              id={`${menuId}-option-${index}`}
              className={`lc-search-select__option${
                index === activeIndex ? ' is-active' : ''
              }`}
              role="option"
              aria-selected={index === activeIndex}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseDown={() => handleSelect(opt)}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
