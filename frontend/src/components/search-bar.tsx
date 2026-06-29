"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, X, Filter, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search...",
  className,
}: SearchInputProps) {
  const [isFocused, setIsFocused] = useState(false);

  return (
    <motion.div
      animate={{ 
        scale: isFocused ? 1.01 : 1,
        boxShadow: isFocused 
          ? "0 0 0 2px var(--primary), 0 4px 12px rgba(0,0,0,0.1)" 
          : "0 0 0 1px var(--border)"
      }}
      transition={{ duration: 0.2 }}
      className={cn(
        "relative flex items-center gap-2 px-4 py-2.5 rounded-xl bg-background border border-border transition-all duration-200",
        isFocused && "border-primary",
        className
      )}
    >
      <Search className={cn(
        "w-4 h-4 shrink-0 transition-colors",
        isFocused ? "text-primary" : "text-muted-foreground"
      )} />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
        className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
      />
      <AnimatePresence>
        {value && (
          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
            onClick={() => onChange("")}
            className="p-1 rounded-md hover:bg-muted transition-colors"
          >
            <X className="w-3.5 h-3.5 text-muted-foreground" />
          </motion.button>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

interface FilterOption {
  value: string;
  label: string;
}

interface FilterDropdownProps {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
  className?: string;
}

export function FilterDropdown({
  label,
  value,
  options,
  onChange,
  className,
}: FilterDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const selectedOption = options.find((opt) => opt.value === value);

  return (
    <div className={cn("relative", className)}>
      <motion.button
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-2 px-4 py-2.5 rounded-xl border border-border bg-background text-sm transition-all duration-200 hover:border-primary/50",
          isOpen && "border-primary"
        )}
      >
        <Filter className="w-4 h-4 text-muted-foreground" />
        <span className="text-muted-foreground">{label}:</span>
        <span className="font-medium">{selectedOption?.label || "All"}</span>
        <motion.div
          animate={{ rotate: isOpen ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          <ChevronDown className="w-4 h-4 text-muted-foreground" />
        </motion.div>
      </motion.button>

      <AnimatePresence>
        {isOpen && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40"
              onClick={() => setIsOpen(false)}
            />
            {/* Dropdown */}
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.15 }}
              className="absolute top-full left-0 mt-2 z-50 w-48 p-2 rounded-xl border border-border bg-popover shadow-lg"
            >
              {options.map((option) => (
                <motion.button
                  key={option.value}
                  whileHover={{ x: 2, backgroundColor: "var(--accent)" }}
                  onClick={() => {
                    onChange(option.value);
                    setIsOpen(false);
                  }}
                  className={cn(
                    "w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left transition-colors",
                    value === option.value && "bg-primary/10 text-primary font-medium"
                  )}
                >
                  {value === option.value && (
                    <motion.div
                      layoutId="filter-indicator"
                      className="w-1.5 h-1.5 rounded-full bg-primary"
                    />
                  )}
                  <span className={value !== option.value ? "ml-3.5" : ""}>
                    {option.label}
                  </span>
                </motion.button>
              ))}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

interface SearchBarProps {
  searchValue: string;
  onSearchChange: (value: string) => void;
  filters?: Array<{
    key: string;
    label: string;
    value: string;
    options: FilterOption[];
    onChange: (value: string) => void;
  }>;
  searchPlaceholder?: string;
  className?: string;
}

export function SearchBar({
  searchValue,
  onSearchChange,
  filters = [],
  searchPlaceholder = "Search...",
  className,
}: SearchBarProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "flex flex-col sm:flex-row items-stretch sm:items-center gap-3",
        className
      )}
    >
      <SearchInput
        value={searchValue}
        onChange={onSearchChange}
        placeholder={searchPlaceholder}
        className="flex-1 min-w-[200px]"
      />
      {filters.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {filters.map((filter) => (
            <FilterDropdown
              key={filter.key}
              label={filter.label}
              value={filter.value}
              options={filter.options}
              onChange={filter.onChange}
            />
          ))}
        </div>
      )}
    </motion.div>
  );
}

// Hook for debounced search
export function useDebouncedSearch(delay: number = 300) {
  const [searchTerm, setSearchTerm] = useState("");
  const [debouncedTerm, setDebouncedTerm] = useState("");

  const handleSearch = useCallback((value: string) => {
    setSearchTerm(value);
  }, []);

  // Debounce effect
  useState(() => {
    const timer = setTimeout(() => {
      setDebouncedTerm(searchTerm);
    }, delay);

    return () => clearTimeout(timer);
  });

  return {
    searchTerm,
    debouncedTerm,
    handleSearch,
  };
}
