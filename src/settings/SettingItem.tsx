import React from 'react';

interface SettingItemProps {
  name?: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
}

/**
 * Renders the inner content of an Obsidian-style setting row.
 * The parent element (with className "setting-item") is supplied by the
 * caller — this component renders the info block and optional control block.
 *
 * Usage:
 *   <div className="setting-item">
 *     <SettingItem name="..." description="...">
 *       <input ... />
 *     </SettingItem>
 *   </div>
 */
export function SettingItem({ name, description, children }: SettingItemProps) {
  return (
    <>
      <div className="setting-item-info">
        {name && <div className="setting-item-name">{name}</div>}
        {description && (
          <div className="setting-item-description">{description}</div>
        )}
      </div>
      {children != null && (
        <div className="setting-item-control">{children}</div>
      )}
    </>
  );
}
