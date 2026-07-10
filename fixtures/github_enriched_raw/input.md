---
title: "jordanbaird/Ice: Powerful menu bar manager for macOS"
source: "https://github.com/jordanbaird/Ice"
source_type: github-project
source_tier: deepwiki
extraction_status: completed
github_owner: jordanbaird
github_repo: Ice
github_stars: 27758
source_fetched_at: "2026-05-05T13:49:53.150816+00:00"
deepwiki_section_count: 31
date: 2026-05-04
type: raw
tags: [menu]
---

# jordanbaird/Ice

_Source: [https://github.com/jordanbaird/Ice](https://github.com/jordanbaird/Ice)_
_Enrichment tier: **deepwiki**_
_Stars: 27758_

_DeepWiki section: index_

Relevant source files 
 
- FREQUENT_ISSUES.md 

- Ice.xcodeproj/project.pbxproj 

- README.md 

- Resources/Icon.fig 

- Resources/Icon.png 

- Resources/rearranging.mov 

 
 
 Ice is a macOS menu bar management tool that provides enhanced control over menu bar items, appearance, and functionality. This document introduces the core concepts, architecture, and primary features of the Ice application. 
 Ice allows users to hide and show menu bar items, customize the menu bar's appearance, and access menu bar items through alternative interfaces such as the Ice Bar. For detailed information on getting started with Ice, see Getting Started . 
 Sources: `README.md` 

## Core Functionality 

 Ice's primary functions include: 
 
- 
 Menu Bar Item Management 
 
 Hide and show menu bar items

- Organize items into sections (visible, hidden, always-hidden)

- Drag and drop interface for rearranging items

- Custom spacing between menu bar items

 
 
- 
 Menu Bar Appearance Customization 
 
 Menu bar tinting (solid colors and gradients)

- Custom menu bar shapes (rounded and/or split)

- Menu bar shadows and borders

 
 
- 
 Alternative Access Interfaces 
 
 Ice Bar - displays hidden items below the menu bar

- Menu bar search functionality

 
 
- 
 User Interaction Methods 
 
 Show hidden items on hover/click/scroll

- Customizable hotkeys

- Automatic rehiding of items

 
 
 
 Sources: `README.md` 

## System Architecture Overview 

 Ice follows a centralized state management architecture with the `AppState` class serving as the central hub. This class coordinates between various subsystems to manage the menu bar, handle events, and maintain application settings. 

### Core Architecture Diagram 

```
 
```

 Sources: `Ice.xcodeproj/project.pbxproj` 

## Menu Bar Organization 

 The menu bar in Ice is conceptually organized into three sections: 
 
- Visible Section : Menu bar items that are always visible

- Hidden Section : Items that are hidden but can be shown through user interaction

- Always Hidden Section : Items that remain hidden unless specifically accessed

 
 Each section has a corresponding control item that manages its visibility state. Items can be moved between sections through a drag-and-drop interface. 

### Menu Bar Organization Diagram 

```
 
```

 Sources: `README.md`, `FREQUENT_ISSUES.md` 

## User Interaction Flow 

 Ice processes user interactions through an event-driven architecture. The `EventManager` captures user events and triggers appropriate actions in the `MenuBarManager`. 

### User Interaction Flow Diagram 

```
 
```

 Sources: `README.md`, `Resources/rearranging.mov` 

## Core System Components 

 Ice's functionality is implemented through several key system components, all coordinated by the central `AppState` class: 

### AppState and Manager Relationships 

```
 
```

 Sources: `Ice.xcodeproj/project.pbxproj`, `README.md` 

### Component Descriptions 

 
- 
 AppState : Central hub that coordinates between all subsystems and maintains application state. 

- 
 MenuBarManager : Manages the menu bar sections and the visibility of menu bar items. Provides access to the Ice Bar and search panel. 

- 
 EventManager : Captures and processes user events like mouse movements, clicks, and keyboard input. 

- 
 PermissionsManager : Handles the required system permissions (accessibility and screen recording). 

- 
 MenuBarItemManager : Manages the caching and manipulation of menu bar item images. 

- 
 MenuBarAppearanceManager : Controls the appearance customization of the menu bar. 

- 
 SettingsManager : Manages application settings and preferences. 

- 
 UpdatesManager : Handles application updates. 

 
 For more detailed information about each component, see Core Systems . 
 Sources: `Ice.xcodeproj/project.pbxproj`, `README.md` 

## Permissions System 

 Ice requires certain system permissions to function properly. The following permissions are essential: 
 
- Accessibility Permission : Required for interacting with menu bar items

- Screen Recording Permission : Required for capturing menu bar item images

 
 When these permissions are missing, Ice shows a permissions window to guide the user through granting them. Some features will have limited functionality until all required permissions are granted. 

### Permissions Flow 

```
 
```

 Sources: `FREQUENT_ISSUES.md` 

## Technical Requirements 

 Ice requires macOS 14 or later due to its use of system APIs that are only available starting in macOS 14. The application interfaces with several external libraries, including: 
 
- AXSwift : For accessibility features

- LaunchAtLogin : For launch at login functionality

- Sparkle : For automatic updates

- CompactSlider : For UI components

- IfritStatic : For additional functionality

 
 Sources: `Ice.xcodeproj/project.pbxproj`, `README.md` 

## Common Issues 

 Some common issues users might encounter when using Ice: 
 
- 
 Items moved to always-hidden section : This happens because macOS adds new items to the far left, which is also where Ice's always-hidden section is located. 

- 
 Missing items : Items might appear to be removed but are likely in the always-hidden section. Option + click the Ice icon to show this section. 

- 
 Menu bar item arrangement : For resolving issues with arranging menu bar items in automatically hidden menu bars, see the Frequent Issues documentation 

 
 For more information on common issues and their solutions, see Getting Started . 
 Sources: `FREQUENT_ISSUES.md` 

## Summary 

 Ice provides a comprehensive solution for managing the macOS menu bar, offering features for hiding, showing, and organizing menu bar items, customizing the menu bar's appearance, and providing alternative access interfaces. Its architecture is centered around a state management pattern with the `AppState` class coordinating between various subsystems to deliver a cohesive user experience. 
 For detailed information on specific aspects of Ice, refer to the related wiki pages in the Core Systems and Menu Bar Features sections. 
 Sources: `README.md` Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Overview 
- Core Functionality 
- System Architecture Overview 
- Core Architecture Diagram 
- Menu Bar Organization 
- Menu Bar Organization Diagram 
- User Interaction Flow 
- User Interaction Flow Diagram 
- Core System Components 
- AppState and Manager Relationships 
- Component Descriptions 
- Permissions System 
- Permissions Flow 
- Technical Requirements 
- Common Issues 
- Summary

---

_DeepWiki section: 1-overview_

Relevant source files 
 
- FREQUENT_ISSUES.md 

- Ice.xcodeproj/project.pbxproj 

- README.md 

- Resources/Icon.fig 

- Resources/Icon.png 

- Resources/rearranging.mov 

 
 
 Ice is a macOS menu bar management tool that provides enhanced control over menu bar items, appearance, and functionality. This document introduces the core concepts, architecture, and primary features of the Ice application. 
 Ice allows users to hide and show menu bar items, customize the menu bar's appearance, and access menu bar items through alternative interfaces such as the Ice Bar. For detailed information on getting started with Ice, see Getting Started . 
 Sources: `README.md` 

## Core Functionality 

 Ice's primary functions include: 
 
- 
 Menu Bar Item Management 
 
 Hide and show menu bar items

- Organize items into sections (visible, hidden, always-hidden)

- Drag and drop interface for rearranging items

- Custom spacing between menu bar items

 
 
- 
 Menu Bar Appearance Customization 
 
 Menu bar tinting (solid colors and gradients)

- Custom menu bar shapes (rounded and/or split)

- Menu bar shadows and borders

 
 
- 
 Alternative Access Interfaces 
 
 Ice Bar - displays hidden items below the menu bar

- Menu bar search functionality

 
 
- 
 User Interaction Methods 
 
 Show hidden items on hover/click/scroll

- Customizable hotkeys

- Automatic rehiding of items

 
 
 
 Sources: `README.md` 

## System Architecture Overview 

 Ice follows a centralized state management architecture with the `AppState` class serving as the central hub. This class coordinates between various subsystems to manage the menu bar, handle events, and maintain application settings. 

### Core Architecture Diagram 

```
 
```

 Sources: `Ice.xcodeproj/project.pbxproj` 

## Menu Bar Organization 

 The menu bar in Ice is conceptually organized into three sections: 
 
- Visible Section : Menu bar items that are always visible

- Hidden Section : Items that are hidden but can be shown through user interaction

- Always Hidden Section : Items that remain hidden unless specifically accessed

 
 Each section has a corresponding control item that manages its visibility state. Items can be moved between sections through a drag-and-drop interface. 

### Menu Bar Organization Diagram 

```
 
```

 Sources: `README.md`, `FREQUENT_ISSUES.md` 

## User Interaction Flow 

 Ice processes user interactions through an event-driven architecture. The `EventManager` captures user events and triggers appropriate actions in the `MenuBarManager`. 

### User Interaction Flow Diagram 

```
 
```

 Sources: `README.md`, `Resources/rearranging.mov` 

## Core System Components 

 Ice's functionality is implemented through several key system components, all coordinated by the central `AppState` class: 

### AppState and Manager Relationships 

```
 
```

 Sources: `Ice.xcodeproj/project.pbxproj`, `README.md` 

### Component Descriptions 

 
- 
 AppState : Central hub that coordinates between all subsystems and maintains application state. 

- 
 MenuBarManager : Manages the menu bar sections and the visibility of menu bar items. Provides access to the Ice Bar and search panel. 

- 
 EventManager : Captures and processes user events like mouse movements, clicks, and keyboard input. 

- 
 PermissionsManager : Handles the required system permissions (accessibility and screen recording). 

- 
 MenuBarItemManager : Manages the caching and manipulation of menu bar item images. 

- 
 MenuBarAppearanceManager : Controls the appearance customization of the menu bar. 

- 
 SettingsManager : Manages application settings and preferences. 

- 
 UpdatesManager : Handles application updates. 

 
 For more detailed information about each component, see Core Systems . 
 Sources: `Ice.xcodeproj/project.pbxproj`, `README.md` 

## Permissions System 

 Ice requires certain system permissions to function properly. The following permissions are essential: 
 
- Accessibility Permission : Required for interacting with menu bar items

- Screen Recording Permission : Required for capturing menu bar item images

 
 When these permissions are missing, Ice shows a permissions window to guide the user through granting them. Some features will have limited functionality until all required permissions are granted. 

### Permissions Flow 

```
 
```

 Sources: `FREQUENT_ISSUES.md` 

## Technical Requirements 

 Ice requires macOS 14 or later due to its use of system APIs that are only available starting in macOS 14. The application interfaces with several external libraries, including: 
 
- AXSwift : For accessibility features

- LaunchAtLogin : For launch at login functionality

- Sparkle : For automatic updates

- CompactSlider : For UI components

- IfritStatic : For additional functionality

 
 Sources: `Ice.xcodeproj/project.pbxproj`, `README.md` 

## Common Issues 

 Some common issues users might encounter when using Ice: 
 
- 
 Items moved to always-hidden section : This happens because macOS adds new items to the far left, which is also where Ice's always-hidden section is located. 

- 
 Missing items : Items might appear to be removed but are likely in the always-hidden section. Option + click the Ice icon to show this section. 

- 
 Menu bar item arrangement : For resolving issues with arranging menu bar items in automatically hidden menu bars, see the Frequent Issues documentation 

 
 For more information on common issues and their solutions, see Getting Started . 
 Sources: `FREQUENT_ISSUES.md` 

## Summary 

 Ice provides a comprehensive solution for managing the macOS menu bar, offering features for hiding, showing, and organizing menu bar items, customizing the menu bar's appearance, and providing alternative access interfaces. Its architecture is centered around a state management pattern with the `AppState` class coordinating between various subsystems to deliver a cohesive user experience. 
 For detailed information on specific aspects of Ice, refer to the related wiki pages in the Core Systems and Menu Bar Features sections. 
 Sources: `README.md` Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Overview 
- Core Functionality 
- System Architecture Overview 
- Core Architecture Diagram 
- Menu Bar Organization 
- Menu Bar Organization Diagram 
- User Interaction Flow 
- User Interaction Flow Diagram 
- Core System Components 
- AppState and Manager Relationships 
- Component Descriptions 
- Permissions System 
- Permissions Flow 
- Technical Requirements 
- Common Issues 
- Summary

---

_DeepWiki section: 1.1-getting-started_

Relevant source files 
 
- .github/ISSUE_TEMPLATE/bug_report.yml 

- .github/ISSUE_TEMPLATE/feature_request.yml 

- FREQUENT_ISSUES.md 

- README.md 

- Resources/Icon.fig 

- Resources/Icon.png 

- Resources/rearranging.mov 

 
 
 This document provides instructions for installing the Ice application and understanding its basic functionality. It is intended for users who want to start using Ice to manage their menu bar on macOS. For information about the architecture and design of Ice, see Project Architecture . 

## What is Ice? 

 Ice is a powerful menu bar management tool for macOS. Its primary function is hiding and showing menu bar items, but it also includes a variety of additional features that make it one of the most versatile menu bar tools available, including: 
 
- Managing menu bar items through sections (visible, hidden, always-hidden)

- Displaying hidden items in a separate bar (the Ice Bar)

- Customizing the menu bar's appearance with tints, borders, and shapes

- Providing keyboard shortcuts for common actions

 
 Sources: README.md 1-18 

## System Requirements 

 Before installing Ice, ensure your system meets the following requirements: 
 
- macOS 14 or later

 
 Ice requires macOS 14+ because it uses several system APIs that are only available starting with this version. 
 Sources: README.md 11-12 README.md 84-86 

## Installation 

 There are two ways to install Ice on your Mac: 

### Manual Installation 

 
- Download the "Ice.zip" file from the latest release 

- Unzip the downloaded file

- Move the Ice application to your Applications folder

 

### Homebrew Installation 

 If you have Homebrew installed on your Mac, you can install Ice using the following command in Terminal: 

```
 
```

 Sources: README.md 24-36 

## Initial Setup 

 When you first launch Ice, it will need certain permissions to function properly. This setup process is essential for Ice to effectively manage your menu bar. 

### Permissions Flow 

```
 
```

### Required Permissions 

 
- Accessibility Permission : Needed to manage menu bar items

- Screen Recording Permission : Required to capture and display menu bar item icons

 
 Without these permissions, Ice will have limited functionality. Grant the permissions when prompted to ensure full functionality. 
 Sources: FREQUENT_ISSUES.md 27-35 

## Understanding Menu Bar Sections 

 Ice organizes the menu bar into three distinct sections, each with its own purpose and behavior. 

```
 
```

 
- Visible Section : Menu bar items in this section are always visible

- Hidden Section : Menu bar items in this section are hidden by default, but can be shown on demand (via hover, click, or keyboard shortcut)

- Always-Hidden Section : Menu bar items in this section are never shown automatically, but can be accessed through the Ice Bar or search

 
 Sources: README.md 40-56 

## Basic Interactions 

 Ice offers several ways to interact with your menu bar items: 

### Menu Bar Interaction Methods 

```
 
```

### Key Interactions 

 
- Hover : Move your cursor over the menu bar to show hidden items

- Click : Click an empty area in the menu bar to show hidden items

- Scroll/Swipe : Scroll or swipe in the menu bar to show/hide hidden items

- Drag and Drop : Command + drag to move items between sections

- Hotkeys : Use configured keyboard shortcuts to show/hide sections

 
 Sources: README.md 42-47 Resources/rearranging.mov 

## Core Features 

### Menu Bar Layout 

 You can organize your menu bar items by dragging and dropping them between sections. This allows you to keep your most used items visible while hiding less frequently used ones. 
 To move an item: 
 
- Hold the Command key

- Drag the item to the desired section

- Release to place the item

 
 To access the Always-Hidden section, Option-click the Ice icon in the menu bar. 

```
 
```

 Sources: README.md 49 FREQUENT_ISSUES.md 9-17 FREQUENT_ISSUES.md 20-22 

### Ice Bar 

 The Ice Bar is an alternative interface for accessing hidden menu bar items, particularly useful for MacBooks with a notch. When enabled, the Ice Bar appears below the menu bar and displays items from the hidden sections. 

```
 
```

 To use the Ice Bar: 
 
- Click the Ice icon in the menu bar to toggle the Ice Bar

- Interact with items in the Ice Bar as you would in the menu bar

 
 Sources: README.md 50 README.md 90-92 

### Menu Bar Search 

 Ice includes a search feature that allows you to quickly find and access any menu bar item, regardless of its section. 
 To use the search: 
 
- Press the configured hotkey (default: Command+Shift+Space)

- Type the name of the item you're looking for

- Select the item from the results to trigger it

 
 Sources: README.md 51 README.md 103-104 

### Menu Bar Appearance 

 Ice allows you to customize the appearance of your menu bar with various visual effects: 
 
- Menu bar tint (solid color or gradient)

- Menu bar shadow

- Menu bar border

- Custom menu bar shapes (rounded corners or split style)

 
 These settings can be accessed through the Appearance tab in Ice's preferences. 
 Sources: README.md 58-64 README.md 98-100 

## Common Issues 

### Automatically Hidden Menu Bar 

 If you see an error message stating "Ice cannot arrange menu bar items in automatically hidden menu bars", follow these steps: 
 
- Open System Settings on your Mac

- Go to Control Center

- Set "Automatically hide and show the menu bar" to "Never"

- Update your Menu Bar Items in Ice

- Return the setting to your preferred configuration

 
 Sources: FREQUENT_ISSUES.md 27-35 

### Items Moving to Always-Hidden Section 

 By default, macOS adds new items to the far left of the menu bar, which is also the location of Ice's always-hidden section. If items unexpectedly appear in the always-hidden section: 
 
- Option + click the Ice icon to show the always-hidden section

- Command + drag the item into a different section

 
 Sources: FREQUENT_ISSUES.md 9-17 FREQUENT_ISSUES.md 20-22 

## Next Steps 

 Now that you have Ice installed and understand its basic functionality, you might want to explore: 
 
- Menu Bar Features - Detailed information about sections, Ice Bar, and search

- Visual Customization - Advanced options for customizing the menu bar appearance

- Settings System - Configure Ice to match your preferences and workflow

 
 Sources: README.md 38-83 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Getting Started 
- What is Ice? 
- System Requirements 
- Installation 
- Manual Installation 
- Homebrew Installation 
- Initial Setup 
- Permissions Flow 
- Required Permissions 
- Understanding Menu Bar Sections 
- Basic Interactions 
- Menu Bar Interaction Methods 
- Key Interactions 
- Core Features 
- Menu Bar Layout 
- Ice Bar 
- Menu Bar Search 
- Menu Bar Appearance 
- Common Issues 
- Automatically Hidden Menu Bar 
- Items Moving to Always-Hidden Section 
- Next Steps

---

_DeepWiki section: 1.2-project-architecture_

Relevant source files 
 
- Ice.xcodeproj/project.pbxproj 

- Ice/Events/EventManager.swift 

- Ice/Main/AppState.swift 

- Ice/MenuBar/MenuBarManager.swift 

- Ice/MenuBar/MenuBarSection.swift 

 
 
 This document provides a high-level explanation of Ice's architecture, component relationships, and key design patterns. It outlines how the different systems interact to provide the application's menu bar management functionality. 
 For details on specific features and user interactions, please refer to Overview . 

## Core Architecture 

 Ice follows a centralized state management approach with distinct manager classes handling specific aspects of functionality. The application is built around the `AppState` class, which coordinates communication between various subsystems. 

```
 
```

 Sources: Ice/Main/AppState.swift 16-37 Ice/MenuBar/MenuBarManager.swift 35-41 Ice/Events/EventManager.swift 21-70 

## Central State Management 

 The `AppState` class serves as the central hub for Ice's state, initializing and coordinating all manager classes. Each manager is provided with a reference to the `AppState`, allowing for communication between subsystems. 

```
 
```

 The `AppState` setup process follows a specific initialization sequence: 
 
- Each manager is lazily initialized with a reference to `AppState`

- The `performSetup()` method calls setup methods on each manager in the correct order

- Combine publishers are configured to propagate state changes between managers

 
 Sources: Ice/Main/AppState.swift 16-37 Ice/Main/AppState.swift 180-192 

## Menu Bar Organization 

 One of Ice's key architectural concepts is how it organizes the menu bar into three distinct sections: 

```
 
```

 Each section has a specific purpose: 

 Section Purpose Control Item Visible Contains items that are always visible Ice Icon Hidden Contains items that can be toggled between visible and hidden Hidden section icon Always-Hidden Contains items that are permanently hidden unless explicitly shown Always-hidden section icon 
 The `MenuBarManager` class stores these sections and provides interfaces (Ice Bar and Search Panel) for accessing hidden menu bar items. 
 Sources: Ice/MenuBar/MenuBarManager.swift 35-41 Ice/MenuBar/MenuBarSection.swift 11-34 Ice/MenuBar/MenuBarSection.swift 109-128 

## Event Flow and User Interaction 

 The `EventManager` captures and processes user interactions with the menu bar through various event monitors: 

```
 
```

 The event handling system supports multiple interaction methods: 

 Interaction Handler Method Action Mouse hover `handleShowOnHover()` Shows hidden items when hovering over menu bar Left click `handleShowOnClick()` Toggles visibility of hidden items Right click `handleShowRightClickMenu()` Shows context menu for customization Scroll wheel `handleShowOnScroll()` Shows/hides items based on scroll direction Drag with Cmd `handleLeftMouseDragged()` Shows all sections for item rearrangement 
 Sources: Ice/Events/EventManager.swift 21-70 Ice/Events/EventManager.swift 146-428 

## Section State Management 

 The `MenuBarSection` class manages the state of each section, controlling when items are shown or hidden: 

```
 
```

 Key section management methods: 
 
- `show()`: Makes the section and its items visible

- `hide()`: Hides the section and its items

- `toggle()`: Toggles between visible and hidden states

 
 If the "Use Ice Bar" setting is enabled, showing a section will display the Ice Bar panel instead of showing items directly in the menu bar. 
 Sources: Ice/MenuBar/MenuBarSection.swift 131-240 

## Dependencies 

 Ice relies on several external libraries for key functionality: 

```
 
```

 These dependencies are managed through Swift Package Manager as seen in the project configuration file. 
 Sources: Ice.xcodeproj/project.pbxproj 390-430 

## Design Patterns 

 Ice implements several key design patterns: 
 
- Centralized State Management : The `AppState` class serves as a central hub for the application's state

- Observer Pattern : Extensive use of Combine for reactive state updates

- Dependency Injection : Managers receive the `AppState` as a dependency

- Command Pattern : User actions are encapsulated in handler methods

 
 The architecture also demonstrates these important principles: 
 
- Separation of Concerns : Each manager handles a specific aspect of functionality

- Single Responsibility : Classes have well-defined responsibilities

- Reactive Programming : State changes propagate through Combine publishers

 

```
 
```

 Sources: Ice/Main/AppState.swift 16-37 Ice/Main/AppState.swift 85-178 Ice/MenuBar/MenuBarManager.swift 84-223 

## Initialization Flow 

 When Ice launches, it follows a specific initialization sequence: 
 
- `AppDelegate` creates the `AppState` instance

- `AppState.performSetup()` initializes all subsystems in the correct order

- Permissions are checked and requested if needed

- Menu bar sections are initialized

- Event monitors are started

- Settings are loaded from persistent storage

 
 This careful initialization sequence ensures that all components are properly configured before user interaction begins. 
 Sources: Ice/Main/AppState.swift 180-192 

## Conclusion 

 Ice's architecture demonstrates a well-organized approach to macOS menu bar management through: 
 
- Centralized state management via the `AppState` class

- Clear separation of concerns with specialized manager classes

- Reactive programming using Combine for state propagation

- Comprehensive event handling for diverse user interactions

- Structured organization of menu bar items into logical sections

 
 This architecture provides a solid foundation for the application's features, allowing for maintainable code and a responsive user experience. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Project Architecture 
- Core Architecture 
- Central State Management 
- Menu Bar Organization 
- Event Flow and User Interaction 
- Section State Management 
- Dependencies 
- Design Patterns 
- Initialization Flow 
- Conclusion

---

_DeepWiki section: 2-core-systems_

Relevant source files 
 
- Ice/Events/EventManager.swift 

- Ice/Main/AppState.swift 

- Ice/MenuBar/MenuBarManager.swift 

- Ice/MenuBar/MenuBarSection.swift 

 
 
 This document provides an overview of the central systems that form the foundation of the Ice application. These core systems manage different aspects of the application's functionality and work together to provide a cohesive user experience. 
 For detailed information about specific core systems, see their dedicated pages: 
 
- AppState 

- Menu Bar Management 

- Event Handling 

- Permissions System 

 

## Central State Architecture 

 At the heart of Ice's architecture is the `AppState` class, which serves as a central hub for the application's state. `AppState` maintains references to various specialized manager classes, each responsible for a specific aspect of the application. 

### Core State Management Diagram 

```
 
```

 Sources: Ice/Main/AppState.swift 10-41 
 The `AppState` is created when the application launches and is passed to the various managers during their initialization. This creates a structured relationship where: 
 
- `AppState` holds references to all managers

- Each manager holds a weak reference back to `AppState` to avoid retain cycles

- Managers can access other managers through the central `AppState`

 

## Manager Architecture 

 Ice uses a manager-based architecture to separate concerns and organize functionality. Each manager is responsible for a specific domain within the application: 

 Manager Responsibility `MenuBarManager` Controls menu bar sections and their visibility `EventManager` Captures user events and handles interactions `PermissionsManager` Manages system permissions needed by the app `SettingsManager` Handles user configuration settings `MenuBarAppearanceManager` Controls visual appearance of the menu bar `MenuBarItemManager` Manages menu bar items and their organization `UpdatesManager` Handles application updates 
 Each manager follows a similar pattern: 
 
- Takes an `AppState` reference during initialization

- Maintains its own internal state

- Implements `performSetup()` to initialize functionality

- Most conform to `ObservableObject` to publish state changes

 
 Sources: Ice/Main/AppState.swift 15-36 

## Menu Bar Management System 

 The menu bar management system is responsible for organizing and controlling the visibility of items in the macOS menu bar. 

### Menu Bar Section Architecture 

```
 
```

 Sources: Ice/MenuBar/MenuBarManager.swift 10-40 Ice/MenuBar/MenuBarSection.swift 8-34 
 Key concepts in the menu bar management system: 
 
- 
 Menu Bar Sections : The menu bar is organized into three sections: 
 
 `visible`: Items that are always shown in the menu bar

- `hidden`: Items that can be shown/hidden by the user

- `alwaysHidden`: Items that are typically kept hidden

 
 
- 
 Control Items : Each section has a control item that manages its visibility 
 
 The Ice icon serves as the control item for the visible section

- Control items appear in the menu bar and have different states (`showItems`, `hideItems`)

 
 
- 
 Alternative Interfaces : The menu bar manager provides additional interfaces: 
 
 `iceBarPanel`: Alternative display for hidden menu bar items

- `searchPanel`: Interface for searching menu bar items

 
 
 
 Sources: Ice/MenuBar/MenuBarManager.swift 34-41 Ice/MenuBar/MenuBarSection.swift 36-108 

## Event Handling System 

 The event handling system captures and processes user input events, directing them to appropriate handlers based on the type of event and context. 

### Event Flow Diagram 

```
 
```

 Sources: Ice/Events/EventManager.swift 18-82 Ice/Events/EventManager.swift 143-428 
 The event handling system consists of: 
 
- 
 Event Monitors : Universal event monitors capture different types of input events 
 
 Each monitor is focused on a specific event type (mouse down, mouse moved, etc.)

- Monitors are started during setup and capture events system-wide

 
 
- 
 Event Handlers : Specialized handlers implement different behaviors 
 
 `handleShowOnClick()`: Shows/hides items when clicking the menu bar

- `handleShowOnHover()`: Shows/hides items when hovering over the menu bar

- `handleShowOnScroll()`: Shows/hides items based on scroll direction

- `handleSmartRehide()`: Intelligently rehides items when user clicks elsewhere

 
 
- 
 Location Detection : Helper methods determine the mouse position context 
 
 `isMouseInsideMenuBar`: Mouse is in the menu bar area

- `isMouseInsideMenuBarItem`: Mouse is over a specific menu bar item

- `isMouseInsideEmptyMenuBarSpace`: Mouse is in empty menu bar space

 
 
 
 Sources: Ice/Events/EventManager.swift 432-550 

## Application Initialization Flow 

 When Ice starts up, it initializes its core systems in a specific sequence: 

```
 
```

 Sources: Ice/Main/AppState.swift 180-192 
 This initialization process ensures that all core systems are properly configured before user interaction begins: 
 
- The application creates an `AppState` instance

- Key components (app delegate, windows) are assigned to the state

- The `performSetup()` method initializes each manager in sequence

- Event monitors begin capturing user input after setup completes

 

## Communication Between Systems 

 The core systems in Ice communicate through several mechanisms: 

### Reactive State Updates 

 Ice uses Combine's publisher-subscriber pattern extensively for reactive updates: 

```
 
```

 Sources: Ice/Main/AppState.swift 156-175 
 When a manager's state changes, it publishes those changes, which the `AppState` observes and forwards to the rest of the system. This ensures that UI and other dependent systems can react appropriately to state changes. 

### Direct Method Calls 

 For immediate actions, systems use direct method calls: 

```
 
```

 Sources: Ice/Events/EventManager.swift 146-176 Ice/MenuBar/MenuBarSection.swift 232-239 
 For example, when handling a click in the menu bar: 
 
- `EventManager` detects the click and calls its handler

- The handler accesses the `MenuBarManager` through `AppState`

- It retrieves the relevant section and calls methods on it

 

## Menu Bar Section States and Transitions 

 The menu bar sections can transition between different states based on user actions: 

```
 
```

 Sources: Ice/MenuBar/MenuBarSection.swift 129-239 
 The transitions between states are managed by: 
 
- `MenuBarSection.show()`: Makes a section and its items visible

- `MenuBarSection.hide()`: Hides a section and its items

- `MenuBarSection.toggle()`: Toggles between showing and hiding

 
 These methods work with both the direct menu bar representation and the Ice Bar alternative interface. 

## Summary 

 The core systems of Ice form a well-structured foundation that enables the application's functionality: 
 
- Central State Management : `AppState` coordinates all subsystems

- Menu Bar Management : Organizes and controls menu bar items

- Event Handling : Captures user input and triggers appropriate actions

- Component Communication : Systems interact through reactive state updates and direct method calls

 
 Together, these systems create a foundation that allows Ice to provide enhanced control over the macOS menu bar, with features like hiding and showing menu bar items, customizing appearance, and providing alternative interfaces. 
 For more detailed information about specific systems, refer to their dedicated pages: 
 
- AppState 

- Menu Bar Management 

- Event Handling 

- Permissions System 

 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Core Systems 
- Central State Architecture 
- Core State Management Diagram 
- Manager Architecture 
- Menu Bar Management System 
- Menu Bar Section Architecture 
- Event Handling System 
- Event Flow Diagram 
- Application Initialization Flow 
- Communication Between Systems 
- Reactive State Updates 
- Direct Method Calls 
- Menu Bar Section States and Transitions 
- Summary

---

_DeepWiki section: 2.1-appstate_

Relevant source files 
 
- Ice/Main/AppState.swift 

- Ice/MenuBar/MenuBarSection.swift 

 
 

## Purpose and Overview 

 The `AppState` class is Ice's central state management component that coordinates all major subsystems of the application. It serves as the hub in a hub-and-spoke architecture, maintaining references to various manager classes that handle specific aspects of the application. This document explains the structure, responsibilities, and interactions of the `AppState` class. 
 For information about menu bar management specifically, see Menu Bar Management . 
 Sources: Ice/Main/AppState.swift 9-11 

## System Architecture 

 `AppState` is designed as a central coordinating entity that: 
 
- Maintains references to all subsystem managers

- Provides a unified interface for app-wide state changes

- Handles window management

- Coordinates system events and state updates

 

### Class Structure 

```
 
```

 Sources: Ice/Main/AppState.swift 16-49 

### Component Relationships 

```
 
```

 Sources: Ice/Main/AppState.swift 16-49 Ice/Main/AppState.swift 180-192 

## Manager Components 

 `AppState` owns and coordinates several specialized manager classes, each responsible for a specific aspect of the application. 

 Manager Type Responsibility `appearanceManager` `MenuBarAppearanceManager` Controls the menu bar's visual appearance `eventManager` `EventManager` Handles system events and user interactions `itemManager` `MenuBarItemManager` Manages menu bar items `menuBarManager` `MenuBarManager` Controls menu bar sections and overall state `permissionsManager` `PermissionsManager` Manages system permissions `settingsManager` `SettingsManager` Handles application settings `updatesManager` `UpdatesManager` Manages application updates `userNotificationManager` `UserNotificationManager` Manages user notifications `imageCache` `MenuBarItemImageCache` Caches menu bar item images `spacingManager` `MenuBarItemSpacingManager` Controls spacing between menu bar items `navigationState` `AppNavigationState` Manages app-wide navigation `hotkeyRegistry` `HotkeyRegistry` Registers and manages hotkeys 
 Sources: Ice/Main/AppState.swift 16-49 

## Initialization and Setup 

 `AppState` uses lazy initialization for its manager components, only creating them when first accessed. The full setup process is completed through the `performSetup()` method. 

```
 
```

 Sources: Ice/Main/AppState.swift 180-192 

## Event Handling and Reactive Programming 

 `AppState` leverages Combine for reactive programming, setting up observers for various system events and propagating state changes throughout the application. 

### Event Flow 

```
 
```

 The `configureCancellables()` method sets up observers for: 
 
- Active space changes

- Frontmost application changes

- Mouse events

- Window visibility changes

 
 Sources: Ice/Main/AppState.swift 85-178 

## Window Management 

 `AppState` is responsible for managing the application's key windows, particularly the settings and permissions windows. 

### Window Management Methods 

 Method Purpose `assignSettingsWindow(_:)` Associates a settings window with the app state `assignPermissionsWindow(_:)` Associates a permissions window with the app state `openSettingsWindow()` Opens the settings window `dismissSettingsWindow()` Closes the settings window `openPermissionsWindow()` Opens the permissions window `dismissPermissionsWindow()` Closes the permissions window 
 Sources: Ice/Main/AppState.swift 203-249 

## Application Activation Management 

 `AppState` provides methods to control the application's activation state: 
 
- `activate(withPolicy:)`: Activates the app with a specified activation policy

- `deactivate(withPolicy:)`: Deactivates the app with a specified activation policy

 
 The activation logic includes special handling for first-time activation via the Dock. 
 Sources: Ice/Main/AppState.swift 252-289 

## Menu Bar Feature Control 

 `AppState` controls the "ShowOnHover" feature for menu bar items: 
 
- `preventShowOnHover()`: Temporarily disables the show-on-hover behavior

- `allowShowOnHover()`: Re-enables the show-on-hover behavior

 
 This is used to prevent unwanted menu bar behavior during certain operations. 
 Sources: Ice/Main/AppState.swift 292-299 

## Interaction with Menu Bar Sections 

 While not directly handling menu bar sections, `AppState` serves as the conduit between the UI and the menu bar management system. The `MenuBarSection` class, which represents different sections of the menu bar (visible, hidden, always-hidden), communicates with `AppState` to access various manager components. 

```
 
```

 Sources: Ice/Main/AppState.swift 25 Ice/MenuBar/MenuBarSection.swift 43-44 

## Integration with SwiftUI 

 `AppState` is designed to integrate with SwiftUI's reactive UI system: 
 
- It's declared as an `@MainActor` to ensure UI updates happen on the main thread

- It adopts `ObservableObject` for SwiftUI state observation

- It propagates `objectWillChange` events from manager classes to update the UI

- It implements `BindingExposable` to facilitate binding creation

 
 Sources: Ice/Main/AppState.swift 10-11 Ice/Main/AppState.swift 156-175 Ice/Main/AppState.swift 303 

## Key State Properties 

 `AppState` tracks several key pieces of state information: 

 Property Type Purpose `isActiveSpaceFullscreen` `Bool` Indicates whether the active space is in fullscreen mode `isShowOnHoverPrevented` `Bool` Controls whether hovering can show hidden menu bar items `setsCursorInBackground` `Bool` Controls whether the app can set the cursor while in the background 
 Sources: Ice/Main/AppState.swift 13 Ice/Main/AppState.swift 61 Ice/Main/AppState.swift 79-82 

## Conclusion 

 The `AppState` class is the central nervous system of the Ice application, coordinating communication between subsystems and maintaining application-wide state. Its hub-and-spoke architecture allows for modular development of features while maintaining a cohesive application state. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - AppState 
- Purpose and Overview 
- System Architecture 
- Class Structure 
- Component Relationships 
- Manager Components 
- Initialization and Setup 
- Event Handling and Reactive Programming 
- Event Flow 
- Window Management 
- Window Management Methods 
- Application Activation Management 
- Menu Bar Feature Control 
- Interaction with Menu Bar Sections 
- Integration with SwiftUI 
- Key State Properties 
- Conclusion

---

_DeepWiki section: 2.2-menu-bar-management_

Relevant source files 
 
- Ice/Events/EventManager.swift 

- Ice/MenuBar/MenuBarManager.swift 

- Ice/Utilities/Predicates.swift 

- Ice/Utilities/RehideStrategy.swift 

 
 
 The Menu Bar Management system is responsible for controlling the visibility, organization, and behavior of menu bar items in the Ice application. This system allows users to hide, show, and organize menu bar items through various interaction methods. 
 For information about menu bar sections and item management, see Sections and Items . For details on visual customizations of the menu bar, see Menu Bar Appearance . 

## System Overview 

 The Menu Bar Management system divides the macOS menu bar into logical sections, manages control items that toggle each section's visibility, and responds to user events to show or hide menu bar items based on configured triggers and strategies. 

```
 
```

 Sources: Ice/MenuBar/MenuBarManager.swift 10-36 Ice/Events/EventManager.swift 9-22 

## Menu Bar Section Structure 

 The Menu Bar Management system organizes the menu bar into three distinct sections: 
 
- Visible Section : Items that are always visible in the menu bar

- Hidden Section : Items that can be shown or hidden on demand

- Always Hidden Section : Items that are hidden by default and can only be accessed through alternative interfaces

 
 Each section is managed by a control item that serves as both a visual indicator of the section's bounds and a control to toggle the section's visibility. 

```
 
```

 Sources: Ice/MenuBar/MenuBarManager.swift 63-80 Ice/Utilities/Predicates.swift 67-117 

## Menu Bar Manager 

 The `MenuBarManager` class is the central component that coordinates the management of menu bar sections and their visibility. It initializes and maintains the three menu bar sections and provides methods to control their state. 

### Key Responsibilities 

 
- Initialize and maintain menu bar sections

- Track the system menu bar visibility state

- Manage the Ice Bar and Search panels

- Handle application menu visibility

- Calculate and store the menu bar's average color for UI integration

- Provide access to sections by name

 

### Core Properties and Methods 

 Property/Method Description `sections` Array of `MenuBarSection` objects representing the three menu bar sections `iceBarPanel` Panel that displays the Ice Bar interface for hidden items `searchPanel` Panel that provides search functionality for menu bar items `averageColorInfo` Information about the menu bar's average color `isMenuBarHiddenBySystem` Boolean indicating if the menu bar is hidden by the system `section(withName:)` Method to retrieve a specific section by name `performSetup()` Initializes the sections and configures event observers `hideApplicationMenus()` Hides application menus when showing hidden items `showApplicationMenus()` Shows application menus when hiding items 
 Sources: Ice/MenuBar/MenuBarManager.swift 10-398 

## Menu Bar Item Positioning 

 Menu bar items are positioned within sections based on their relative coordinates in the menu bar. The system uses predicates to determine which section an item belongs to based on its position relative to the control items. 

```
 
```

 Sources: Ice/Utilities/Predicates.swift 67-117 Ice/MenuBar/MenuBarManager.swift 57-80 

## Event Handling and User Interactions 

 The `EventManager` class is responsible for monitoring user interactions with the menu bar and triggering the appropriate actions based on configured settings. It handles various types of interactions: 

### Show/Hide Triggers 

 
- Show on Hover : Shows hidden items when the mouse hovers over an empty area of the menu bar

- Show on Click : Shows hidden items when the user clicks on an empty area of the menu bar

- Show on Scroll : Shows or hides items based on scroll direction in the menu bar

- User Dragging : Shows all sections when the user drags items with the Command key pressed

 

### Auto-Rehide Strategies 

 The system provides several strategies for automatically hiding menu bar items after they've been shown: 

 Strategy Description Smart Uses algorithms to determine when to rehide items based on user interactions with other applications Timed Hides items after a specified time interval Focused App Hides items when the focused application changes 
 Sources: Ice/Events/EventManager.swift 142-428 Ice/Utilities/RehideStrategy.swift 8-27 

## Detection and Tracking 

 The system features sophisticated detection capabilities to determine where the mouse is located relative to menu bar elements: 

```
 
```

 Sources: Ice/Events/EventManager.swift 432-549 

## Integration with Alternative Interfaces 

 The Menu Bar Management system integrates with two alternative interfaces for accessing menu bar items: 

### Ice Bar 

 The Ice Bar panel provides an alternative interface for accessing hidden menu bar items without needing to show them in the menu bar. 

### Search Panel 

 The Menu Bar Search panel allows users to search for menu bar items by name and activate them directly. 
 Both interfaces are created and managed by the `MenuBarManager` and are accessible through properties on that class. 
 Sources: Ice/MenuBar/MenuBarManager.swift 37-41 Ice/MenuBar/MenuBarManager.swift 50-52 

## Application Menu Management 

 When items are shown or hidden, the system can also manage the visibility of application menus to provide additional space for menu bar items. This feature is controlled by user settings and is implemented through the following methods: 
 
- `hideApplicationMenus()`: Activates the app to hide application menus

- `showApplicationMenus()`: Deactivates the app to show application menus

- `toggleApplicationMenus()`: Toggles between hidden and shown states

 
 This functionality considers factors such as: 
 
- Whether the "HideApplicationMenus" setting is enabled

- If the menu bar is hidden by the system

- If the active space is fullscreen

- If the settings window is visible

 
 Sources: Ice/MenuBar/MenuBarManager.swift 151-222 Ice/MenuBar/MenuBarManager.swift 352-381 

## Average Color Information 

 The system tracks the average color of the menu bar for visual integration with menu bar appearance customizations. The `updateAverageColorInfo()` method analyzes the menu bar window or desktop wallpaper to determine the average color, which can then be used for UI theming. 
 Sources: Ice/MenuBar/MenuBarManager.swift 226-274 Ice/MenuBar/MenuBarManager.swift 404-414 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Menu Bar Management 
- System Overview 
- Menu Bar Section Structure 
- Menu Bar Manager 
- Key Responsibilities 
- Core Properties and Methods 
- Menu Bar Item Positioning 
- Event Handling and User Interactions 
- Show/Hide Triggers 
- Auto-Rehide Strategies 
- Detection and Tracking 
- Integration with Alternative Interfaces 
- Ice Bar 
- Search Panel 
- Application Menu Management 
- Average Color Information

---

_DeepWiki section: 2.3-event-handling_

Relevant source files 
 
- Ice/Events/EventManager.swift 

- Ice/MenuBar/MenuBarManager.swift 

 
 
 The Event Handling system in Ice is responsible for capturing and responding to user interactions, such as mouse movements, clicks, scrolls, and keyboard inputs. This system forms the foundation for Ice's interactive features, allowing users to control the menu bar through various input methods. 
 For information about the general application architecture, see Project Architecture . 

## System Overview 

 The Event Handling system uses event monitors to detect user interactions and trigger appropriate responses in the menu bar. It primarily manages showing and hiding menu bar items based on user actions, while respecting user-configurable settings. 

```
 
```

 Sources: Ice/Events/EventManager.swift 10-123 Ice/MenuBar/MenuBarManager.swift 10-49 

## EventManager Class 

 The `EventManager` class is the central component of the Event Handling system. It initializes and manages various event monitors, processes captured events, and coordinates with other systems to respond to user interactions. 

```
 
```

 Key responsibilities of the `EventManager`: 
 
- Initializing event monitors for different types of user interactions

- Setting up event handlers to process captured events

- Starting and stopping monitors as needed

- Communicating with other systems to trigger appropriate actions

 
 Sources: Ice/Events/EventManager.swift 10-139 

## Event Monitors 

 The `EventManager` maintains five different event monitors to capture various user interactions: 

 Monitor Event Types Purpose `mouseDownMonitor` `.leftMouseDown`, `.rightMouseDown` Captures mouse button clicks `mouseUpMonitor` `.leftMouseUp` Captures mouse button releases `mouseDraggedMonitor` `.leftMouseDragged` Captures mouse dragging actions `mouseMovedMonitor` `.mouseMoved` Captures mouse movement `scrollWheelMonitor` `.scrollWheel` Captures scrolling actions 
 Each monitor is an instance of `UniversalEventMonitor` initialized with specific event masks and handler functions. The monitors can be started or stopped collectively using the `startAll()` and `stopAll()` methods. 
 Sources: Ice/Events/EventManager.swift 18-81 Ice/Events/EventManager.swift 127-139 

## Event Handlers 

 Event handlers are methods that process captured events and determine the appropriate responses. The `EventManager` implements several handlers for different scenarios: 

```
 
```

### Key Event Handlers 

 
- 
 `handleShowOnClick()` : Toggles the visibility of menu bar sections when clicking in empty menu bar space, depending on modifier keys pressed. 

- 
 `handleSmartRehide()` : Intelligently hides menu bar sections when the user clicks outside the menu bar, based on the rehide strategy setting. 

- 
 `handleShowOnHover()` : Shows or hides menu bar sections based on mouse hover, with a configurable delay. 

- 
 `handleShowOnScroll()` : Shows or hides menu bar sections based on scroll direction (up to show, down to hide). 

- 
 `handleLeftMouseDragged()` : Handles dragging menu bar items, showing all sections when Command key is held during dragging. 

 
 Sources: Ice/Events/EventManager.swift 146-428 

## Mouse Position Detection 

 A crucial aspect of the Event Handling system is determining the mouse position relative to various UI elements. The `EventManager` provides several helper properties for this purpose: 

 Helper Property Purpose `isMouseInsideMenuBar` Checks if mouse is within the menu bar's bounds `isMouseInsideApplicationMenu` Checks if mouse is over system application menus `isMouseInsideMenuBarItem` Checks if mouse is over any menu bar item `isMouseInsideNotch` Checks if mouse is in the notch area (on notched displays) `isMouseInsideEmptyMenuBarSpace` Checks if mouse is in empty menu bar space `isMouseInsideIceBar` Checks if mouse is over the Ice Bar panel `isMouseInsideIceIcon` Checks if mouse is over the Ice control icon 
 These properties are used extensively by event handlers to determine when and how to respond to user interactions. 
 Sources: Ice/Events/EventManager.swift 432-550 

## Integration with Menu Bar Management 

 The Event Handling system works closely with the Menu Bar Management system to control the visibility of menu bar items and sections. 

```
 
```

 The integration involves: 
 
- Accessing the `menuBarManager` through the `appState` to manipulate menu bar sections

- Retrieving specific sections using `section(withName:)` (e.g., `.visible`, `.hidden`, `.alwaysHidden`)

- Calling methods on these sections to show, hide, or toggle their visibility

- Triggering special actions like showing context menus or handling drag operations

 
 Sources: Ice/Events/EventManager.swift 147-265 Ice/MenuBar/MenuBarManager.swift 395-397 

## Settings-Dependent Behavior 

 The Event Handling system's behavior is highly dependent on user-configurable settings. Most event handlers check relevant settings before taking action, allowing users to customize how the app responds to events. 

 Setting Event Handler Effect `showOnHover` `handleShowOnHover()` Enables/disables showing menu bar items on hover `showOnClick` `handleShowOnClick()` Enables/disables showing menu bar items on click `showOnScroll` `handleShowOnScroll()` Enables/disables showing menu bar items on scroll `autoRehide` `handleSmartRehide()` Enables/disables automatic rehiding of menu bar items `rehideStrategy` `handleSmartRehide()`, `configureCancellables()` Determines when and how items are rehidden `showOnHoverDelay` `handleShowOnHover()` Sets delay before showing/hiding on hover `showAllSectionsOnUserDrag` `handleLeftMouseDragged()` Controls visibility during drag operations 
 This integration with the Settings system allows for a highly customizable user experience while maintaining the core functionality of the Event Handling system. 
 Sources: Ice/Events/EventManager.swift 149-152 Ice/Events/EventManager.swift 181-184 Ice/Events/EventManager.swift 348-357 Ice/Events/EventManager.swift 406-408 Ice/Events/EventManager.swift 328-329 

## Event Flow Lifecycle 

 The following diagram illustrates the complete lifecycle of an event from user interaction to UI response: 

```
 
```

 This lifecycle ensures that user interactions are processed consistently and efficiently, with appropriate checks at each stage to determine if and how the system should respond. 
 Sources: Ice/Events/EventManager.swift 146-428 

## Fullscreen Space Handling 

 The Event Handling system includes special handling for fullscreen spaces, where the menu bar behavior differs from standard spaces: 
 
- In fullscreen spaces, the menu bar is typically hidden and slides down when the mouse moves to the top of the screen

- The `configureCancellables()` method sets up special observers for fullscreen spaces

- When in a fullscreen space, the system uses a different approach to detect if the mouse is inside the menu bar

- The `handleShowOnHover()` handler is triggered when the hidden section's control item frame changes in fullscreen mode

 
 This ensures consistent behavior across different macOS space types and menu bar visibility states. 
 Sources: Ice/Events/EventManager.swift 97-119 Ice/Events/EventManager.swift 447-465 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Event Handling 
- System Overview 
- EventManager Class 
- Event Monitors 
- Event Handlers 
- Key Event Handlers 
- Mouse Position Detection 
- Integration with Menu Bar Management 
- Settings-Dependent Behavior 
- Event Flow Lifecycle 
- Fullscreen Space Handling

---

_DeepWiki section: 2.4-permissions-system_

Relevant source files 
 
- Ice/Main/AppDelegate.swift 

- Ice/Permissions/Permission.swift 

- Ice/Permissions/PermissionsManager.swift 

- Ice/Permissions/PermissionsView.swift 

- Ice/Permissions/PermissionsWindow.swift 

- Ice/Utilities/ScreenCapture.swift 

 
 
 This document describes the architecture and functionality of Ice's Permissions System. The system is responsible for managing, checking, and requesting macOS system permissions required by the application to function properly. Specifically, it handles Accessibility and Screen Recording permissions that allow Ice to interact with and customize the menu bar. 

## Overview and Purpose 

 Ice requires macOS system permissions to provide its core functionality. The Permissions System handles: 
 
- Checking if required permissions are granted

- Requesting permissions from the user

- Monitoring permissions status changes

- Providing a UI for explaining permissions to users

- Enabling appropriate functionality based on available permissions

 
 The system distinguishes between required and optional permissions, with some features gracefully degrading when optional permissions aren't available. 
 Sources: 
 
- Ice/Permissions/Permission.swift 11-114 

- Ice/Permissions/PermissionsManager.swift 9-21 

 

## System Architecture 

 The Permissions System consists of several interconnected components: 

```
 
```

 Diagram: Permissions System Class Structure 
 Sources: 
 
- Ice/Permissions/Permission.swift 16-114 

- Ice/Permissions/PermissionsManager.swift 10-78 

- Ice/Permissions/PermissionsView.swift 8-196 

- Ice/Permissions/PermissionsWindow.swift 8-25 

 

## Permission Types 

 Ice requires two types of macOS permissions: 

 Permission Type Purpose System Function Accessibility Required - Get real-time menu bar information
- Arrange menu bar items `checkIsProcessTrusted()` Screen Recording Optional - Edit menu bar appearance
- Display images of menu bar items `ScreenCapture.checkPermissions()` 

### Accessibility Permission 

 This is a required permission that allows Ice to access and control UI elements programmatically. Without this permission, the core functionality of viewing and arranging menu bar items cannot work. 

```
 
```

 Diagram: Accessibility Permission Flow 
 Sources: 
 
- Ice/Permissions/Permission.swift 116-136 

 

### Screen Recording Permission 

 This is an optional permission that allows Ice to capture images of the screen, which enables menu bar appearance customization and displaying images of individual menu bar items. 

```
 
```

 Diagram: Screen Recording Permission Flow 
 Sources: 
 
- Ice/Permissions/Permission.swift 138-158 

- Ice/Utilities/ScreenCapture.swift 11-55 

 

## Permissions State Management 

 The `PermissionsManager` tracks the overall state of permissions through its `permissionsState` property: 

```
 
```

 Diagram: Permissions State Transitions 
 Sources: 
 
- Ice/Permissions/PermissionsManager.swift 13-70 

 
 The `PermissionsManager` continuously monitors the state of individual permissions using Combine publishers and updates the overall permissions state accordingly. 

## Permission Check and Request Lifecycle 

### Permission Checking 

 Each `Permission` instance runs a timer that periodically checks if its permission is granted: 

```
 
```

 Diagram: Permission Check Lifecycle 
 Sources: 
 
- Ice/Permissions/Permission.swift 66-77 

- Ice/Permissions/PermissionsManager.swift 46-70 

 

### Screen Recording Permission Checks 

 The `ScreenCapture` utility provides methods to check for screen recording permissions: 
 
- `checkPermissions()` - Tries to read menu bar item titles to determine if permissions are granted

- `cachedCheckPermissions()` - Caches the permission check result to avoid repeated checks

 
 Sources: 
 
- Ice/Utilities/ScreenCapture.swift 11-44 

 

## Permission Request Flow 

 When a user needs to grant permissions, the following flow occurs: 

```
 
```

 Diagram: Permission Request Flow 
 Sources: 
 
- Ice/Permissions/Permission.swift 80-105 

- Ice/Permissions/PermissionsView.swift 156-166 

 

## Integration with Application Lifecycle 

 The Permissions System is integrated with the application lifecycle in the `AppDelegate`: 

```
 
```

 Diagram: Application Startup Permission Flow 
 Sources: 
 
- Ice/Main/AppDelegate.swift 41-59 

 

## Permissions UI 

 The Permissions System includes a dedicated UI for requesting and explaining permissions to users: 
 
- `PermissionsWindow` - A SwiftUI scene that creates the permissions window

- `PermissionsView` - The main view that displays permission information and request buttons

 
 Key features of the permissions UI: 
 
- Displays explanatory text about each permission

- Shows whether a permission is required or optional

- Provides buttons to request each permission

- Shows the current status of each permission

- Allows the user to continue with limited functionality if only required permissions are granted

 
 Sources: 
 
- Ice/Permissions/PermissionsView.swift 8-196 

- Ice/Permissions/PermissionsWindow.swift 8-25 

 

## Limited Mode Functionality 

 When only required permissions are granted (Accessibility but not Screen Recording), Ice operates in a limited mode: 
 
- Core functionality of managing menu bar items works

- Menu bar appearance customization is disabled

- Display of menu bar item images may be limited

 
 The UI indicates this limited mode status to users with a yellow "Continue in Limited Mode" button, ensuring they're aware of the functional limitations. 
 Sources: 
 
- Ice/Permissions/PermissionsView.swift 12-26 

- Ice/Permissions/PermissionsView.swift 176-190 

 

## Technical Implementation Details 

### Permission Monitoring 

 Each `Permission` instance uses a timer-based check with Combine publishers to monitor permission status: 
 
- Timer publishes every 1 second

- Permission check function is called

- Status is updated and published to subscribers

- `PermissionsManager` combines these publishers to determine overall permissions state

 
 Sources: 
 
- Ice/Permissions/Permission.swift 66-77 

- Ice/Permissions/PermissionsManager.swift 46-70 

 

### Async Permission Waiting 

 The `waitForPermission()` method provides an async way to wait until a permission is granted: 

```
 
```

 This allows UI code to await permission changes before proceeding with UI updates. 
 Sources: 
 
- Ice/Permissions/Permission.swift 88-105 

 

## Conclusion 

 The Permissions System provides a comprehensive framework for managing macOS permissions required by Ice. It handles permission checking, requesting, monitoring, and UI presentation in a clean, modular way that integrates well with the rest of the application. 
 The design allows the application to function with varying levels of permissions, gracefully degrading features when optional permissions aren't available while requiring essential permissions for core functionality. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Permissions System 
- Overview and Purpose 
- System Architecture 
- Permission Types 
- Accessibility Permission 
- Screen Recording Permission 
- Permissions State Management 
- Permission Check and Request Lifecycle 
- Permission Checking 
- Screen Recording Permission Checks 
- Permission Request Flow 
- Integration with Application Lifecycle 
- Permissions UI 
- Limited Mode Functionality 
- Technical Implementation Details 
- Permission Monitoring 
- Async Permission Waiting 
- Conclusion

---

_DeepWiki section: 3-menu-bar-features_

Relevant source files 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 

- Ice/MenuBar/Search/MenuBarSearchPanel.swift 

- Ice/MenuBar/Spacing/MenuBarItemSpacingManager.swift 

- Ice/Settings/SettingsPanes/MenuBarLayoutSettingsPane.swift 

- Ice/UI/IceBar/IceBar.swift 

- Ice/UI/LayoutBar/LayoutBar.swift 

- Ice/UI/LayoutBar/LayoutBarContainer.swift 

- Ice/UI/LayoutBar/LayoutBarItemView.swift 

- Ice/UI/LayoutBar/LayoutBarPaddingView.swift 

- Ice/UI/LayoutBar/LayoutBarScrollView.swift 

- Ice/Utilities/Logging.swift 

- Ice/Utilities/MouseCursor.swift 

 
 
 This document provides an overview of the primary features Ice offers for managing and interacting with the macOS menu bar. For information about the underlying systems that power these features, see Core Systems and Menu Bar Management . 

## Overview 

 Ice provides several key features for enhancing the macOS menu bar experience: 
 
- Menu Bar Sections - Organize menu bar items into visible, hidden, and always-hidden sections

- Ice Bar - A floating panel that displays hidden menu bar items

- Menu Bar Search - A search interface for finding and interacting with menu bar items

- Layout Customization - Drag-and-drop interface for rearranging menu bar items

 
 These features work together to solve the common problem of menu bar overcrowding while maintaining easy access to all menu bar items. 

## Menu Bar Organization 

 Ice organizes menu bar items into three distinct sections: 

 Section Purpose Control Item Visible Items that are always shown in the menu bar Visible Control Item Hidden Items that are hidden but accessible via the Ice Bar Hidden Control Item Always Hidden Items that are never shown in the menu bar but accessible via the Ice Bar and Search Always Hidden Control Item 

### Menu Bar Section Structure 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 12-68 

- Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 100-114 

 

## Ice Bar 

 The Ice Bar is a floating panel that provides quick access to menu bar items that have been hidden from the main menu bar. It's particularly useful for accessing less frequently used items without cluttering the menu bar. 

### Ice Bar Features 

 
- Displays hidden and always-hidden menu bar items

- Appears beneath the menu bar when activated

- Automatically positions itself relative to the Ice icon or mouse pointer

- Matches the menu bar appearance

- Provides visual feedback when menu bar items are clicked

 

### Ice Bar Workflow 

```
 
```

 Sources: 
 
- Ice/UI/IceBar/IceBar.swift 11-190 

- Ice/UI/IceBar/IceBar.swift 232-348 

- Ice/UI/IceBar/IceBar.swift 350-490 

 

### Ice Bar Positioning 

 The Ice Bar can be positioned in one of several locations: 
 
- Dynamic - Positions based on whether the mouse is over an empty menu bar space

- Mouse Pointer - Positions directly under the current mouse location

- Ice Icon - Positions relative to the Ice icon in the menu bar

 
 Positioning is managed by the `updateOrigin(for:)` method in the `IceBarPanel` class Ice/UI/IceBar/IceBar.swift 100-152 

## Menu Bar Search 

 The Menu Bar Search feature provides a search interface for finding and interacting with menu bar items across all sections (visible, hidden, and always-hidden). 

### Search Panel Features 

 
- Search across all menu bar items by name

- Displays app icons alongside menu bar items

- Keyboard navigation support

- Direct click access to any menu bar item

- Visual preview of how items appear in the menu bar

 

### Search Panel Workflow 

```
 
```

 Sources: 
 
- Ice/MenuBar/Search/MenuBarSearchPanel.swift 11-144 

- Ice/MenuBar/Search/MenuBarSearchPanel.swift 174-293 

- Ice/MenuBar/Search/MenuBarSearchPanel.swift 396-469 

 

### Search Implementation 

 The search implementation uses a fuzzy search algorithm provided by the Fuse library, allowing users to find items even with partial or imprecise queries. The search panel groups results by section and dynamically updates as the user types. 

## Menu Bar Layout Customization 

 Ice provides a powerful interface for customizing the layout of menu bar items through drag-and-drop interactions. 

### Layout Features 

 
- Drag-and-drop interface for rearranging items

- Visual representation of menu bar items

- Separate control for each section (visible, hidden, always-hidden)

- Real-time updates to the actual menu bar

 

### Layout Components 

```
 
```

 Sources: 
 
- Ice/UI/LayoutBar/LayoutBar.swift 8-64 

- Ice/UI/LayoutBar/LayoutBarItemView.swift 11-177 

- Ice/UI/LayoutBar/LayoutBarPaddingView.swift 10-143 

- Ice/UI/LayoutBar/LayoutBarContainer.swift 10-302 

 

### Drag and Drop Implementation 

 The layout system implements a custom drag-and-drop interface that handles: 
 
- Item visualization with appropriate icons

- Drag source and destination tracking

- Visual feedback during dragging

- Actual menu bar item movement

 
 When an item is dropped, the `move(item:to:)` method in `LayoutBarPaddingView` Ice/UI/LayoutBar/LayoutBarPaddingView.swift 127-142 calls into the `MenuBarItemManager` to perform the actual movement of the menu bar item. 

## Menu Bar Item Management 

 The core functionality for managing menu bar items is implemented in the `MenuBarItemManager` class, which provides methods for: 
 
- Caching items - Maintains an up-to-date cache of menu bar items

- Moving items - Moves items between sections or changes their order

- Temporarily showing items - Makes hidden items temporarily visible

- Managing item events - Handles events related to menu bar items

 

### Menu Bar Item Movement 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 482-561 

- Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 565-654 

 

## Menu Bar Item Spacing 

 Ice also provides control over the spacing between menu bar items, which can be adjusted to make the menu bar more compact or more spread out. 
 The `MenuBarItemSpacingManager` class Ice/MenuBar/Spacing/MenuBarItemSpacingManager.swift 10-215 handles this functionality by: 
 
- Modifying system UserDefaults that control menu bar item spacing

- Restarting affected applications to apply the changes

- Handling special cases for system applications like Control Center

 

## Integration with Ice Settings 

 The Menu Bar Features integrate with Ice's settings system through the `MenuBarLayoutSettingsPane` Ice/Settings/SettingsPanes/MenuBarLayoutSettingsPane.swift 8-90 which provides a user interface for: 
 
- Viewing and managing all menu bar items

- Visualizing the current layout

- Rearranging items through drag and drop

- Handling permission errors and other edge cases

 

### Settings Integration 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsPanes/MenuBarLayoutSettingsPane.swift 8-90 

- Ice/UI/LayoutBar/LayoutBar.swift 8-64 

 

## Technical Requirements and Permissions 

 Ice's menu bar features rely on several system permissions to function properly: 
 
- Accessibility permissions - Required for interacting with menu bar items

- Screen recording permissions - Required for capturing images of menu bar items

 
 If these permissions are not granted, the Ice Bar and Menu Bar Search features will display appropriate error messages guiding the user to enable the necessary permissions. 
 Both the Ice Bar Ice/UI/IceBar/IceBar.swift 311-325 and Menu Bar Search Ice/MenuBar/Search/MenuBarSearchPanel.swift 104-106 check for these permissions before attempting to display menu bar items. 

## Summary 

 Ice's menu bar features provide a comprehensive solution for managing and interacting with menu bar items on macOS. By organizing items into sections, providing alternative access methods like the Ice Bar and Search, and enabling customization through layout tools, Ice addresses the common problem of menu bar overcrowding while keeping all items easily accessible. 
 The implementation relies on a combination of macOS APIs, custom UI components, and careful event handling to provide a seamless user experience that integrates well with the native macOS menu bar. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Menu Bar Features 
- Overview 
- Menu Bar Organization 
- Menu Bar Section Structure 
- Ice Bar 
- Ice Bar Features 
- Ice Bar Workflow 
- Ice Bar Positioning 
- Menu Bar Search 
- Search Panel Features 
- Search Panel Workflow 
- Search Implementation 
- Menu Bar Layout Customization 
- Layout Features 
- Layout Components 
- Drag and Drop Implementation 
- Menu Bar Item Management 
- Menu Bar Item Movement 
- Menu Bar Item Spacing 
- Integration with Ice Settings 
- Settings Integration 
- Technical Requirements and Permissions 
- Summary

---

_DeepWiki section: 3.1-sections-and-items_

Relevant source files 
 
- Ice/MenuBar/ControlItem/ControlItem.swift 

- Ice/MenuBar/ControlItem/ControlItemImage.swift 

- Ice/MenuBar/ControlItem/ControlItemImageSet.swift 

- Ice/MenuBar/MenuBarItems/MenuBarItem.swift 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 

- Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift 

- Ice/Utilities/BindingExposable.swift 

- Ice/Utilities/RehideStrategy.swift 

 
 
 This document explains how Ice organizes the macOS menu bar into sections and manages the individual menu bar items within those sections. It covers the core concepts of section organization, control items that manage sections, and the representation of menu bar items. For information about the Ice Bar feature, see Ice Bar . For information about the auto-hide behavior, see Auto-Hide Behavior . 

## Menu Bar Organization 

 Ice organizes the macOS menu bar into three distinct sections, each serving a specific purpose for menu bar item management. This organization allows users to control which items are visible and which are hidden, providing a cleaner and more customized menu bar experience. 

### Menu Bar Sections 

 The menu bar is divided into the following sections: 

 Section Name Constant Purpose Default Visibility Visible `.visible` Contains items that are always visible in the menu bar Visible Hidden `.hidden` Contains items that are hidden by default but can be shown Hidden Always Hidden `.alwaysHidden` Contains items that are always hidden unless explicitly shown Hidden 
 Each section is managed by a dedicated control item that serves as the interface for showing or hiding the section's contents. These control items appear as status items in the menu bar and provide a visual indication of the section's state. 
 Sources: Ice/MenuBar/ControlItem/ControlItem.swift 10-563 

### Menu Bar Section Structure 

```
 
```

 Sources: Ice/MenuBar/ControlItem/ControlItem.swift 13-17 

## Control Items 

 Control items are status items in the menu bar that serve as the interface for managing sections. Each section has its own control item with a unique identifier. 

### Control Item Types 

```
 
```

 Control items are responsible for: 
 
- Section Management : Each control item controls the visibility of its associated section.

- Visual Indication : Control items change their appearance to indicate whether their section is currently visible or hidden.

- User Interaction : Control items respond to user clicks to toggle their associated section's visibility.

 
 Control items can be in one of two states: 
 
- `hideItems`: The associated section's items are hidden

- `showItems`: The associated section's items are visible

 
 Control items have different visual appearances based on the section they control: 
 
- The Ice Icon (for the visible section) displays a customizable icon

- Hidden Section Control Item shows a large chevron

- Always Hidden Section Control Item shows a small chevron

 
 Sources: Ice/MenuBar/ControlItem/ControlItem.swift 10-563 Ice/MenuBar/ControlItem/ControlItemImage.swift 9-94 Ice/MenuBar/ControlItem/ControlItemImageSet.swift 7-84 

### Control Item Appearance 

 Control items can display different images based on their state. The Ice icon (visible section control) can be customized with various image sets: 

 Image Set Hidden State Visible State Arrow Left-facing arrow Right-facing arrow Chevron Left-facing chevron Right-facing chevron Door Closed door Open door Dot Filled dot Stroke dot (default) Ellipsis Filled ellipsis Stroke ellipsis Ice Cube Stroke ice cube Filled ice cube Sunglasses Filled sunglasses Stroke sunglasses Custom User-provided image User-provided image 
 Sources: Ice/MenuBar/ControlItem/ControlItemImageSet.swift 7-84 

## Menu Bar Items 

 Menu bar items are the individual elements that appear in the macOS menu bar. Ice provides a representation of these items through the `MenuBarItem` and `MenuBarItemInfo` classes. 

### Menu Bar Item Structure 

```
 
```

### Menu Bar Item Properties 

 Each menu bar item has properties that determine how it can be managed: 
 
- Movability : Some items like the Clock, Siri, and Control Center cannot be moved due to macOS restrictions

- Hideability : Some items like the Audio/Video Module, FaceTime, and Music Recognition cannot be hidden

- Ownership : Items are associated with the applications that created them

- Display Name : A user-friendly name derived from the item's title, application name, or other attributes

 
 Sources: Ice/MenuBar/MenuBarItems/MenuBarItem.swift 10-240 Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift 7-221 

### Special Menu Bar Items 

 Ice defines several special menu bar items: 

 Item Description Constraints Ice Icon The control item for the visible section Can be customized Hidden Control Item The control item for the hidden section Functions as a section divider Always Hidden Control Item The control item for the always-hidden section Functions as a section divider Clock The system clock Cannot be moved Siri The Siri menu bar item Cannot be moved Control Center The Control Center (BentoBox) Cannot be moved Audio/Video Module Appears during screen/audio recording Cannot be hidden FaceTime The FaceTime menu bar item Cannot be hidden Music Recognition The Shazam music recognition item Cannot be hidden 
 Sources: Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift 32-103 

## Item Caching and Visualization 

 To display menu bar items in interfaces like the Ice Bar or settings window, Ice maintains a cache of menu bar item images. 

### Menu Bar Item Image Cache 

 The `MenuBarItemImageCache` class captures and stores images of menu bar items to provide a visual representation of items in other parts of the application. 

```
 
```

 The image cache is updated: 
 
- Every 3 seconds at minimum

- When the active space or screen parameters change

- When the average menu bar color changes

- When the cached items change

 
 Sources: Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 10-288 

## User Interaction Flow 

### Toggling Section Visibility 

```
 
```

 The visibility of menu bar sections can be toggled in several ways: 
 
- Clicking directly on the control item

- Using the context menu of a control item

- Using configured hotkeys

- Through the Ice Bar interface

- Using the menu bar search feature

 
 Sources: Ice/MenuBar/ControlItem/ControlItem.swift 390-541 

## Auto-Hide and Rehide Strategies 

 Ice provides mechanisms to automatically hide menu bar items based on different strategies. This enhances the clean appearance of the menu bar while ensuring items are available when needed. 

### Rehide Strategy Options 

 Strategy Description Smart Menu bar items are rehidden using an intelligent algorithm that considers user behavior Timed Menu bar items are rehidden after a specific time interval Focused App Menu bar items are rehidden when the focused application changes 
 For more detailed information about auto-hide behavior, see Auto-Hide Behavior . 
 Sources: Ice/Utilities/RehideStrategy.swift 7-27 

## Integration with Other Components 

 The sections and items system integrates with other Ice components: 
 
- The Ice Bar : Displays menu bar items from hidden sections in a floating panel. See Ice Bar .

- Menu Bar Search : Allows searching and interacting with menu bar items. See Menu Bar Search .

- Settings System : Provides user interface for configuring section behavior and item organization. See Menu Bar Layout .

- Hotkey System : Enables keyboard shortcuts for toggling sections. See Hotkeys .

 
 The section system plays a central role in Ice's functionality, serving as the framework for all menu bar customization features. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Sections and Items 
- Menu Bar Organization 
- Menu Bar Sections 
- Menu Bar Section Structure 
- Control Items 
- Control Item Types 
- Control Item Appearance 
- Menu Bar Items 
- Menu Bar Item Structure 
- Menu Bar Item Properties 
- Special Menu Bar Items 
- Item Caching and Visualization 
- Menu Bar Item Image Cache 
- User Interaction Flow 
- Toggling Section Visibility 
- Auto-Hide and Rehide Strategies 
- Rehide Strategy Options 
- Integration with Other Components

---

_DeepWiki section: 3.2-ice-bar_

Relevant source files 
 
- Ice/MenuBar/Search/MenuBarSearchPanel.swift 

- Ice/Settings/SettingsPanes/MenuBarLayoutSettingsPane.swift 

- Ice/UI/IceBar/IceBar.swift 

- Ice/UI/LayoutBar/LayoutBar.swift 

- Ice/Utilities/MouseCursor.swift 

 
 

## Purpose and Scope 

 The Ice Bar is a core feature of Ice that provides an alternative interface for accessing hidden and always-hidden menu bar items. It appears as a floating panel below the menu bar, allowing users to interact with menu bar items that would otherwise be hidden from view. This document covers the technical implementation of the Ice Bar, its components, positioning mechanics, and interaction handling. 
 For information about the Menu Bar Search feature, which serves a similar but distinct purpose, see Menu Bar Search . 

## Technical Overview 

 The Ice Bar is implemented as a custom panel (`IceBarPanel`) that displays menu bar items from hidden sections. When triggered, it captures the current state of menu bar items, renders them in a horizontally scrollable interface, and allows users to interact with these items as if they were directly clicking on the menu bar. 

```
 
```

 Diagram: Ice Bar Component Relationships 
 Sources: Ice/UI/IceBar/IceBar.swift 11-190 Ice/UI/IceBar/IceBar.swift 194-227 Ice/UI/IceBar/IceBar.swift 232-348 

## Core Components 

### IceBarPanel 

 The `IceBarPanel` is the foundation of the Ice Bar feature. It's an `NSPanel` subclass that manages the visibility, positioning, and appearance of the Ice Bar. 
 Key properties and methods: 
 
- `currentSection`: Tracks which section of menu bar items is being displayed

- `colorManager`: Manages the colors displayed in the Ice Bar

- `show(section:on:)`: Shows the Ice Bar for a specific section on a given screen

- `close()`: Closes the Ice Bar and resets its state

- `updateOrigin(for:)`: Updates the position of the Ice Bar based on its location strategy

 

```
 
```

 Diagram: IceBarPanel Class Structure 
 Sources: Ice/UI/IceBar/IceBar.swift 11-190 

### IceBarContentView 

 The `IceBarContentView` is the main SwiftUI view that composes the visual elements of the Ice Bar. It manages: 
 
- Display of permission warnings when required

- Handling of unsupported states (e.g., automatically hidden menu bars)

- Rendering menu bar items in a horizontal scroll view

- Converting cached images to appropriate format for display

 
 Sources: Ice/UI/IceBar/IceBar.swift 232-348 

### IceBarItemView and IceBarItemClickView 

 These components work together to display individual menu bar items and handle user interactions: 
 
- `IceBarItemView`: Renders the menu bar item's image and sets up click handlers

- `IceBarItemClickView`: A lower-level NSView representative that captures precise click events and differentiates between left and right mouse clicks

 
 Sources: Ice/UI/IceBar/IceBar.swift 350-490 

## Functionality 

### Showing the Ice Bar 

 When the Ice Bar is triggered (typically via a hotkey or clicking the Ice control item), the following sequence occurs: 

```
 
```

 Diagram: Ice Bar Activation Sequence 
 Sources: Ice/UI/IceBar/IceBar.swift 154-182 

### Positioning Strategies 

 The Ice Bar can be positioned using different strategies specified by the `IceBarLocation` enum: 
 
- Dynamic : Positions based on mouse location (if inside empty menu bar space) or the Ice icon

- Mouse Pointer : Positions centered on the current mouse location

- Ice Icon : Positions centered on the Ice control item in the menu bar

 

```
 
```

 Diagram: Ice Bar Positioning Strategies 
 Sources: Ice/UI/IceBar/IceBar.swift 100-152 

### Item Display and Interaction 

 The Ice Bar displays menu bar items in a horizontal scroll view. When a user clicks on an item: 
 
- The Ice Bar panel closes

- The clicked item is temporarily shown in the menu bar

- A simulated click is performed on the actual menu bar item

 
 This creates a seamless experience where the user can interact with hidden menu bar items as if they were directly clicking on them in the menu bar. 

```
 
```

 Diagram: Ice Bar Item Interaction Flow 
 Sources: Ice/UI/IceBar/IceBar.swift 350-490 Ice/UI/IceBar/IceBar.swift 414-490 

## Integration with Other Systems 

### AppState Integration 

 The Ice Bar is tightly integrated with the AppState, which provides access to: 
 
- MenuBarManager for section management

- ItemManager for menu bar item information

- ImageCache for retrieving item images

- NavigationState for tracking UI state

- SettingsManager for appearance and positioning preferences

 
 Sources: Ice/UI/IceBar/IceBar.swift 12-28 Ice/UI/IceBar/IceBar.swift 101-102 

### Event Handling 

 The Ice Bar panel responds to various system events: 
 
- Space changes (closes when the active space changes)

- Screen parameter changes

- Menu bar visibility changes

- Frame changes (updates positioning)

 

```
 
```

 Diagram: Ice Bar Event Handling 
 Sources: Ice/UI/IceBar/IceBar.swift 44-97 

## Technical Details 

### Appearance Management 

 The Ice Bar adopts the appearance of the menu bar, following the user's customization settings: 
 
- Background colors/gradients match menu bar settings

- Shape and border configuration follows menu bar settings

- Text color adapts based on background brightness

 
 The appearance is managed through: 
 
- `IceBarColorManager` which provides color information

- `layoutBarStyle` modifier which applies consistent styling

- Dynamic adaptation to light/dark content based on background brightness

 
 Sources: Ice/UI/IceBar/IceBar.swift 286-295 

### Permission Requirements 

 The Ice Bar requires screen recording permissions to function properly. This is because: 
 
- It needs to capture images of menu bar items

- It needs to identify the positions of items for interaction

 
 When permissions are missing, the Ice Bar displays a message instructing the user to grant permissions through the Ice Settings. 
 Sources: Ice/UI/IceBar/IceBar.swift 311-325 

## Comparison with Menu Bar Search 

 While both the Ice Bar and Menu Bar Search provide alternative interfaces for accessing menu bar items, they serve different purposes: 

 Feature Ice Bar Menu Bar Search Primary Purpose Quick access to hidden items Search and discovery of items UI Style Horizontal bar below menu bar Floating panel with search field Interaction Model Visual browsing Text-based search Location Anchored to menu bar Centered on screen Display Format Original item appearance List with item details User Trigger Typically hotkey or click Typically search hotkey 
 The Ice Bar complements the Menu Bar Search feature, providing a more visual and direct way to access hidden menu bar items. 
 Sources: Ice/UI/IceBar/IceBar.swift 232-348 Ice/MenuBar/Search/MenuBarSearchPanel.swift 11-144 Ice/MenuBar/Search/MenuBarSearchPanel.swift 174-310 

## Usage Considerations 

### Performance 

 The Ice Bar is designed for efficiency, with several optimizations: 
 
- Lazy loading of images

- Caching of menu bar items

- Event debouncing to prevent excessive updates

- Minimal redraws through conditional view updates

 

### Edge Cases 

 The Ice Bar handles several edge cases gracefully: 
 
- Automatically hidden menu bars : Displays a message that items can't be shown

- Missing permissions : Provides guidance to grant screen recording permissions

- Cache failures : Shows an error message if item caching fails

- Screen bounds : Ensures the panel remains within screen boundaries

- Menu bar height variations : Adapts to different menu bar heights, including notched displays

 
 Sources: Ice/UI/IceBar/IceBar.swift 310-347 Ice/UI/IceBar/IceBar.swift 262-271 

## Summary 

 The Ice Bar is a sophisticated feature that extends the functionality of the macOS menu bar by providing an alternative interface for accessing hidden menu bar items. Its implementation demonstrates careful integration with macOS UI patterns, robust handling of system events, and thoughtful consideration of user experience details. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Ice Bar 
- Purpose and Scope 
- Technical Overview 
- Core Components 
- IceBarPanel 
- IceBarContentView 
- IceBarItemView and IceBarItemClickView 
- Functionality 
- Showing the Ice Bar 
- Positioning Strategies 
- Item Display and Interaction 
- Integration with Other Systems 
- AppState Integration 
- Event Handling 
- Technical Details 
- Appearance Management 
- Permission Requirements 
- Comparison with Menu Bar Search 
- Usage Considerations 
- Performance 
- Edge Cases 
- Summary

---

_DeepWiki section: 3.3-menu-bar-search_

Relevant source files 
 
- Ice/MenuBar/Search/MenuBarSearchPanel.swift 

- Ice/Settings/SettingsPanes/MenuBarLayoutSettingsPane.swift 

- Ice/UI/IceBar/IceBar.swift 

- Ice/UI/LayoutBar/LayoutBar.swift 

- Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 

- Ice/UI/ViewModifiers/LocalEventMonitorModifier.swift 

- Ice/UI/ViewModifiers/OnKeyDown.swift 

- Ice/UI/Views/SectionedList.swift 

- Ice/Utilities/MouseCursor.swift 

 
 
 The Menu Bar Search feature provides a convenient way for users to find and interact with menu bar items through a searchable interface. This feature is particularly useful when working with hidden menu bar items or when trying to quickly access a specific menu bar item without navigating through the menu bar. 
 For information about the Ice Bar feature, which provides an alternative way to access hidden menu bar items, see Ice Bar . 

## Overview 

 The Menu Bar Search feature allows users to: 
 
- Search for menu bar items by name across all menu bar sections

- View menu bar items with their icons

- Navigate search results using keyboard shortcuts

- Click on menu bar items directly from the search interface

 

```
 
```

 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 10-144 Ice/MenuBar/Search/MenuBarSearchPanel.swift 174-310 

## Architecture 

 The Menu Bar Search feature is implemented as a panel window containing a search interface that interacts with the menu bar items through the application's state management system. 

```
 
```

 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 11-144 Ice/MenuBar/Search/MenuBarSearchPanel.swift 146-172 Ice/MenuBar/Search/MenuBarSearchPanel.swift 174-310 Ice/UI/Views/SectionedList.swift 10-156 

## Components 

### MenuBarSearchPanel 

 The `MenuBarSearchPanel` is the main container for the search interface. It's implemented as an `NSPanel` subclass with specific styling and behaviors. 
 Key features: 
 
- Appears as a floating panel above other windows

- Can be toggled with a hotkey

- Handles keyboard and mouse events

- Automatically updates when menu bar items change

- Dismisses when clicking outside or pressing Escape

 
 The panel creates and manages a `MenuBarSearchHostingView` that contains the SwiftUI-based search interface. 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 11-144 

### Search Interface 

 The search interface is implemented by `MenuBarSearchContentView` and includes: 
 
- Search field - For entering search queries

- Results list - Shows matching menu bar items organized by section

- Action buttons - Controls at the bottom of the panel

 

 UI Component Purpose TextField Input field for search queries SectionedList Displays search results in sections Settings Button Opens Ice settings window Show Item Button Activates the selected menu bar item 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 174-310 

### Search Functionality 

 The search feature uses the Fuse library for fuzzy searching, which enables: 
 
- Matching items even with partial or imprecise queries

- Ranking results by relevance

- Fast performance even with many items

 
 When a user types in the search field: 
 
- The `searchText` state variable is updated

- `updateDisplayedItems()` is called to filter the list

- The first matching item is automatically selected

- The list view is updated to show only matching items

 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 246-293 

## User Interaction Flow 

```
 
```

 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 95-143 Ice/MenuBar/Search/MenuBarSearchPanel.swift 294-310 

## Item Visualization 

 Menu bar items are displayed in the search results with their icons, making them easily recognizable. The `MenuBarSearchItemView` component is responsible for rendering each item in the results list. 
 Each search result item displays: 
 
- The application icon that owns the menu bar item

- The item name

- A visual representation of the menu bar item

 
 The search panel retrieves item images from the `MenuBarItemImageCache`, which maintains cached images of menu bar items. 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 405-469 

## Keyboard Navigation 

 The Menu Bar Search panel supports full keyboard navigation: 

 Key Action Up Arrow Move selection to previous item Down Arrow Move selection to next item Return Activate selected item Escape Dismiss search panel 
 This is implemented using event monitors and view modifiers: 
 
- `keyDownMonitor` in `MenuBarSearchPanel` handles Escape to close the panel

- `onKeyDown` modifiers in `SectionedList` handle navigation keys

- A focus state ensures the search field is selected when the panel appears

 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 40-49 Ice/UI/Views/SectionedList.swift 77-95 Ice/UI/ViewModifiers/OnKeyDown.swift 8-20 

## Item Activation 

 When a user selects an item and activates it (by pressing Return or double-clicking), the following process occurs: 
 
- The search panel is closed

- A brief delay allows the panel to fully dismiss

- The `MenuBarItemManager.tempShowItem()` method is called

- The selected menu bar item is temporarily shown (if hidden)

- A mouse click is simulated on the item

 
 This process allows users to interact with menu bar items that may be hidden in the menu bar, making all functionality accessible through the search interface. 

```
 
```

 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 304-310 

## Integration with AppState 

 The Menu Bar Search feature integrates with the Ice application's state management system through: 
 
- AppState - The central state container that provides access to all managers

- MenuBarItemManager - Manages menu bar items and provides methods to interact with them

- MenuBarItemImageCache - Caches images of menu bar items for display in the search interface

- NavigationState - Tracks whether the search panel is currently presented

 
 When the search panel is shown: 
 
- `appState.navigationState.isSearchPresented` is set to `true`

- `appState.imageCache.updateCache()` is called to ensure item images are up-to-date

 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 95-106 Ice/MenuBar/Search/MenuBarSearchPanel.swift 140-143 

## Technical Implementation Details 

### Panel Configuration 

 The `MenuBarSearchPanel` is configured with specific window properties: 
 
- `nonactivatingPanel` - Prevents the panel from taking focus from other applications

- `titled`, `fullSizeContentView`, `utilityWindow`, `hudWindow` - Styling attributes

- `level = .floating` - Makes the panel appear above regular windows

- `collectionBehavior = [.fullScreenAuxiliary, .ignoresCycle, .moveToActiveSpace]` - Controls how the panel behaves with spaces and full-screen applications

 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 56-69 

### Event Handling 

 The panel uses several event monitoring mechanisms: 
 
- `mouseDownMonitor` - Closes the panel when clicking outside

- `keyDownMonitor` - Handles Escape key to close the panel

- `Publishers.Merge` with notification center publishers - Closes the panel when spaces change or screen parameters change

 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 24-49 Ice/MenuBar/Search/MenuBarSearchPanel.swift 83-90 Ice/UI/ViewModifiers/LocalEventMonitorModifier.swift 8-48 

### Search Implementation 

 The search functionality is implemented using the Fuse library with these key aspects: 
 
- A threshold of 0.5 determines how close a match needs to be

- Search is performed across all menu bar sections unless they are disabled

- Results are filtered and sorted by relevance

 
 When no search query is entered, all items are displayed, organized by section. 
 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 188-292 

## Usage Example 

 To use the Menu Bar Search feature: 
 
- Activate the search panel using the configured hotkey

- Type a search query to find specific menu bar items

- Use arrow keys to navigate through results or click to select an item

- Press Return or double-click to activate the selected item

- Press Escape to dismiss the search panel without action

 
 Note that the search panel positions itself in the upper middle of the screen by default: 

```
 
```

 Sources: Ice/MenuBar/Search/MenuBarSearchPanel.swift 115-118 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Menu Bar Search 
- Overview 
- Architecture 
- Components 
- MenuBarSearchPanel 
- Search Interface 
- Search Functionality 
- User Interaction Flow 
- Item Visualization 
- Keyboard Navigation 
- Item Activation 
- Integration with AppState 
- Technical Implementation Details 
- Panel Configuration 
- Event Handling 
- Search Implementation 
- Usage Example

---

_DeepWiki section: 3.4-auto-hide-behavior_

Relevant source files 
 
- Ice/Events/EventManager.swift 

- Ice/MenuBar/MenuBarManager.swift 

- Ice/Utilities/RehideStrategy.swift 

 
 
 This page documents the auto-hide behavior system in Ice, which controls how menu bar items are automatically shown and hidden based on user interactions. This is one of Ice's core features that allows for a cleaner menu bar experience by showing items only when needed. 
 For information about the overall menu bar organization and sections, see Sections and Items . For details about Ice Bar (an alternative way to access hidden items), see Ice Bar . 

## Overview 

 The auto-hide behavior in Ice consists of two main components: 
 
- Show triggers - Methods to make hidden items appear (hover, click, scroll)

- Rehide strategies - Ways to automatically hide items again when they're no longer needed

 
 These behaviors are managed primarily by the `EventManager` class, which monitors mouse and keyboard events, and coordinates with the `MenuBarManager` to show and hide appropriate menu bar sections. 

```
 
```

 Sources: Ice/Events/EventManager.swift 345-427 Ice/MenuBar/MenuBarManager.swift 10-398 

## Show Triggers 

 Ice provides three primary ways to trigger the display of hidden menu bar items: 

### Show on Hover 

 When enabled, hovering the mouse over an empty space in the menu bar will show hidden items after a configurable delay. 

```
 
```

 The hover detection is implemented in the `handleShowOnHover()` method: 
 Sources: Ice/Events/EventManager.swift 345-396 

### Show on Click 

 When enabled, clicking on an empty space in the menu bar will toggle the visibility of hidden items. 

```
 
```

 Sources: Ice/Events/EventManager.swift 146-176 

### Show on Scroll 

 When enabled, scrolling in the menu bar area can show or hide items: 
 
- Scrolling up shows hidden items

- Scrolling down hides visible items

 

```
 
```

 Sources: Ice/Events/EventManager.swift 398-427 

## Rehide Strategies 

 Ice provides three strategies for automatically hiding menu bar items when they're no longer needed, defined in the `RehideStrategy` enum: 

```
 
```

 Sources: Ice/Utilities/RehideStrategy.swift 8-27 

### Smart Rehide 

 The smart rehide strategy uses an intelligent algorithm to determine when to hide menu bar items. It triggers when: 
 
- User clicks outside the menu bar

- Clicked on an active window with a regular activation policy

- Not clicking into the Ice Bar

 
 This provides a natural feel where items hide when you start interacting with another application. 

```
 
```

 The implementation checks various conditions to determine when it's appropriate to hide the menu bar sections. 
 Sources: Ice/Events/EventManager.swift 178-252 

### Timed Rehide 

 The timed rehide strategy automatically hides menu bar items after a specific time interval, regardless of what the user is doing. The implementation of this is not shown in the provided code snippets, but it's defined as an option in the `RehideStrategy` enum. 

### Focused App Rehide 

 The focused app strategy hides menu bar items when the user switches to a different application. This is implemented using a Combine publisher that observes changes to the frontmost application: 

```
 
```

 Sources: Ice/MenuBar/MenuBarManager.swift 118-135 

## Mouse Position Detection 

 A critical component of the auto-hide behavior is accurately detecting the mouse position relative to various UI elements. The `EventManager` provides several helper methods for this: 

```
 
```

 These properties are used throughout the auto-hide logic to determine when to show or hide menu bar items based on precise mouse position. 
 Sources: Ice/Events/EventManager.swift 432-549 

## Preventing Unintended Show on Hover 

 Ice includes mechanisms to prevent unintended showing of menu bar items. For example, when clicking on menu bar items, the system temporarily prevents the show-on-hover behavior to avoid interfering with normal menu interactions: 

```
 
```

 Sources: Ice/Events/EventManager.swift 267-302 

## Integration with System Menu Bar State 

 The auto-hide behavior also takes into account the system menu bar state, such as whether the menu bar is being hidden by macOS (in fullscreen mode or with auto-hide menu bar enabled): 

```
 
```

 Sources: Ice/MenuBar/MenuBarManager.swift 87-115 

## User Configuration 

 The auto-hide behavior can be customized through user settings, managed by the settings system. The main settings that affect auto-hide behavior include: 

 Setting Description Location `showOnHover` Enable showing items on mouse hover General Settings `showOnClick` Enable showing items on mouse click General Settings `showOnScroll` Enable showing items on mouse scroll General Settings `autoRehide` Enable auto-rehiding of items General Settings `rehideStrategy` Strategy used for rehiding (.smart, .timed, .focusedApp) General Settings `showOnHoverDelay` Delay before showing/hiding on hover (seconds) Advanced Settings 
 These settings allow users to fine-tune the auto-hide behavior according to their preferences. 
 Sources: Ice/Events/EventManager.swift 345-396 Ice/Events/EventManager.swift 178-252 

## Special Cases 

### Fullscreen Spaces 

 The auto-hide behavior adapts to fullscreen spaces, where the menu bar behavior is different. In fullscreen mode, the menu bar slides down from the top on hover, and Ice monitors the frame of control items to detect this: 

```
 
```

 Sources: Ice/Events/EventManager.swift 100-119 

### Dragging Menu Bar Items 

 When users are dragging menu bar items (with Command key pressed), Ice can show all sections, including section dividers, to allow for reorganization: 

```
 
```

 Sources: Ice/Events/EventManager.swift 312-343 

## Interaction with Application Menus 

 Ice can hide application menus (the menu items on the left side of the menu bar) when showing hidden sections. This behavior is controlled by the `hideApplicationMenus` setting: 

```
 
```

 Sources: Ice/MenuBar/MenuBarManager.swift 152-223 Ice/MenuBar/MenuBarManager.swift 352-381 

## Summary 

 Ice's auto-hide behavior provides a flexible system for showing and hiding menu bar items based on user interaction. The combination of multiple show triggers (hover, click, scroll) and rehide strategies (smart, timed, focused app) allows for a highly customizable experience. The implementation carefully handles various edge cases to ensure a smooth user experience across different system states and screen configurations. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Auto-Hide Behavior 
- Overview 
- Show Triggers 
- Show on Hover 
- Show on Click 
- Show on Scroll 
- Rehide Strategies 
- Smart Rehide 
- Timed Rehide 
- Focused App Rehide 
- Mouse Position Detection 
- Preventing Unintended Show on Hover 
- Integration with System Menu Bar State 
- User Configuration 
- Special Cases 
- Fullscreen Spaces 
- Dragging Menu Bar Items 
- Interaction with Application Menus 
- Summary

---

_DeepWiki section: 4-visual-customization_

Relevant source files 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditorPanel.swift 

- Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 

- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 

- Ice/UI/IceUI/IceGroupBox.swift 

- Ice/UI/IceUI/IceSection.swift 

- Ice/Utilities/SystemAppearance.swift 

 
 
 The Visual Customization system allows users to modify the appearance of the macOS menu bar through Ice. This document covers the architectural components, configuration options, and user interfaces related to menu bar visual customization. For information about managing menu bar items and sections, see Menu Bar Features . 

## System Overview 

 The Visual Customization system lets users apply various visual effects to the menu bar, including: 
 
- Tinting with solid colors or gradients

- Applying custom shapes with rounded or square corners

- Adding borders with customizable colors and widths

- Adding shadows

- Setting different appearances for light and dark modes

 
 These customizations are applied through an overlay panel that sits on top of the native menu bar. 

```
 
```

 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 10-155 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 8-130 

- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 11-356 

 

## Architecture Components 

### MenuBarAppearanceManager 

 This class is the central manager for all menu bar appearance customizations. It: 
 
- Maintains the current appearance configuration

- Creates and manages overlay panels for each screen

- Persists appearance settings to UserDefaults

- Provides preview capabilities for appearance changes

 

```
 
```

 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 10-155 

- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 11-356 

 

### MenuBarOverlayPanel 

 This component is an `NSPanel` subclass that sits on top of the menu bar to visually alter its appearance. It: 
 
- Captures the desktop wallpaper beneath the menu bar

- Tracks the application menu frame position and size

- Draws the configured visual effects (tint, shape, border, shadow)

- Handles multiple screens and space changes

 
 The panel uses a content view (`MenuBarOverlayPanelContentView`) that performs the actual drawing of the visual effects. 
 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 11-356 

- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 360-793 

 

## Configuration Options 

### Dynamic vs Static Appearance 

 Ice allows you to configure either: 
 
- A single appearance that applies regardless of macOS light/dark mode (static)

- Separate appearances for light and dark modes (dynamic)

 
 This is controlled by the `isDynamic` property in the configuration. 

### Shape Options 

 The menu bar can have three different shape styles: 
 
- None : Standard rectangular menu bar

- Full : A single shape with customizable end caps

- Split : Separate shapes for the application menu and status items

 
 For screens with a notch (like newer MacBooks), an inset option is available to better accommodate the notch. 

```
 
```

 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 463-617 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 71-129 

 

### Tint Options 

 Three tint options are available: 
 
- None : No tinting applied

- Solid : A single color with adjustable opacity

- Gradient : A customizable gradient with adjustable opacity

 
 The tint is applied as a semi-transparent overlay (20% opacity) to maintain visibility of menu bar items. 
 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 630-644 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 147-178 

 

### Border and Shadow 

 Additional visual effects include: 
 
- Border : Customizable color and width (1-3px)

- Shadow : A subtle drop shadow beneath the menu bar or shaped region

 
 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 676-786 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 180-215 

 

## UI Components 

### MenuBarAppearanceEditor 

 The appearance editor provides a user interface for customizing the menu bar appearance. It can be displayed in two locations: 
 
- In the Settings window (as a dedicated pane)

- In a popover accessed by right-clicking an empty area of the menu bar

 

```
 
```

 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 8-328 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditorPanel.swift 10-116 

 

### Preview Capability 

 When using dynamic appearance settings, the editor provides "Hold to Preview" buttons that let users see how their settings will look in the other appearance mode without having to switch the system appearance. 
 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 218-328 

- Ice/Utilities/SystemAppearance.swift 8-83 

 

## Implementation Details 

### Overlay Panel Rendering Process 

 The overlay panel uses a multi-step rendering process: 
 
- Create a path for the desired shape (full or split)

- Apply wallpaper clip mask to show the desktop behind the menu bar where appropriate

- Apply shadow if enabled

- Apply tint (solid or gradient) within the shape boundaries

- Apply border if enabled

 

```
 
```

 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 646-787 

 

### Multi-Screen Support 

 The system creates separate overlay panels for each connected screen: 
 
- Each panel tracks its own screen's menu bar

- When screens change, panels are recreated as needed

- Each panel captures its own desktop wallpaper and menu bar dimensions

 

```
 
```

 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 62-146 

- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 81-247 

 

### System Appearance Changes 

 The system monitors for appearance changes (light/dark mode transitions) and updates accordingly: 
 
- Listens for `interfaceThemeChangedNotification`

- Determines current system appearance

- Applies the appropriate configuration based on dynamic settings

 
 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 114-129 

- Ice/Utilities/SystemAppearance.swift 8-83 

 

## UI Organization 

 The appearance editor is organized using custom UI components: 
 
- `IceSection`: Organizes controls into logical sections

- `IceGroupBox`: Provides visual grouping with borders

- `UnlabeledPartialEditor`: Core editor for a single configuration

- `LabeledPartialEditor`: Editor with preview button for dynamic mode

 
 These components create a consistent experience across the different locations where the editor can appear. 
 Sources: 
 
- Ice/UI/IceUI/IceSection.swift 8-137 

- Ice/UI/IceUI/IceGroupBox.swift 8-103 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 132-217 

 

## User Access Points 

 There are two ways to access the appearance editor: 
 
- Settings window : Available through the main application settings

- Right-click menu bar : Quick access by right-clicking an empty area of the menu bar

 
 The right-click method uses a popover positioned directly beneath the menu bar for convenient editing. 
 Sources: 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditorPanel.swift 10-116 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 17-33 

 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Visual Customization 
- System Overview 
- Architecture Components 
- MenuBarAppearanceManager 
- MenuBarOverlayPanel 
- Configuration Options 
- Dynamic vs Static Appearance 
- Shape Options 
- Tint Options 
- Border and Shadow 
- UI Components 
- MenuBarAppearanceEditor 
- Preview Capability 
- Implementation Details 
- Overlay Panel Rendering Process 
- Multi-Screen Support 
- System Appearance Changes 
- UI Organization 
- User Access Points

---

_DeepWiki section: 4.1-menu-bar-appearance_

Relevant source files 
 
- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 

- Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditorPanel.swift 

- Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 

- Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 

- Ice/UI/IceUI/IceGroupBox.swift 

- Ice/UI/IceUI/IceSection.swift 

- Ice/Utilities/SystemAppearance.swift 

 
 
 This document covers Ice's menu bar appearance customization system, which allows users to modify the visual appearance of the macOS menu bar. It includes tinting, borders, shadows, and custom shapes for the menu bar. For information about gradient and color selection UI components used within this system, see Gradient and Color Selection . 

## System Overview 

 The menu bar appearance system enables users to visually transform the standard macOS menu bar by applying custom shapes, colors, and effects. It works by placing transparent overlay panels on top of the system menu bar and rendering visual elements based on user configuration. 

```
 
```

 Sources: Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 1-165 Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 1-794 Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 1-328 

## Key Components 

 The menu bar appearance system consists of several interconnected classes: 

 Component Description Role `MenuBarAppearanceManager` Core manager class Manages configurations and overlay panels `MenuBarOverlayPanel` NSPanel subclass Visually modifies the menu bar appearance `MenuBarAppearanceEditor` SwiftUI view Provides user interface for appearance editing `MenuBarAppearanceEditorPanel` NSPanel subclass Manages the appearance editor popover UI `SystemAppearance` Enum Represents light and dark appearances 

```
 
```

 Sources: Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 10-164 Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 11-356 Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 8-130 

## Configuration Model 

 The appearance configuration is structured as a hierarchical model that supports different settings for light and dark modes: 

```
 
```

 The configuration determines: 
 
- Whether to use different settings for light/dark mode (`isDynamic`)

- The shape of the menu bar (`shapeKind`): none (standard), full (one shape), or split (two separate shapes)

- Whether to inset the shape on notched displays (`isInset`)

- The tint style (`tintKind`): none, solid color, or gradient

- Whether to include shadows and/or borders

- Border color and width

 
 Sources: Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 13-16 Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 361-362 

## Overlay Panel Mechanism 

 The menu bar appearance is modified using transparent overlay panels that sit on top of the system menu bar. 

```
 
```

 Key aspects of the overlay panel: 
 
- Panel Creation : One panel is created for each screen

- Content Drawing : The panel's content view draws visual elements based on the current configuration

- Update Triggers : The panel updates in response to:
 
 Screen parameter changes

- Light/dark mode changes

- Space changes

- Application menu frame changes

- Manual configuration changes

 
 
 
 Sources: Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 11-356 Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 360-793 Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 97-147 

## Shape Rendering Process 

 The system supports three menu bar shape types: 
 
- None : Standard menu bar without custom shape

- Full : Single continuous shape with configurable end caps

- Split : Two separate shapes for the left and right parts of the menu bar

 

```
 
```

 When drawing a shape: 
 
- The system determines which shape kind to use

- For full shapes, it creates a path with the specified end caps

- For split shapes, it creates two separate paths based on:
 
 Application menu width (for the leading part)

- Menu bar items position (for the trailing part)

 
 
- The shape is then drawn with tint, shadow, and border effects

 
 Sources: Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 463-617 Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 646-787 

## User Interface 

 The appearance customization UI is provided through the `MenuBarAppearanceEditor` and can be accessed in two ways: 
 
- In the application settings window

- Through a popover that appears when right-clicking an empty area in the menu bar

 

```
 
```

 The editor includes: 
 
- A toggle for dynamic (appearance-dependent) configuration

- Controls for tint configuration (none, solid color, gradient)

- Shadow toggle

- Border controls (toggle, color picker, width selector)

- Shape picker with options for full or split shapes

- Inset toggle for notched displays

- Preview buttons for seeing temporary effects without saving

 
 Sources: Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 8-328 Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditorPanel.swift 11-115 

## Dynamic Appearance 

 The system supports different configurations for light and dark modes when the `isDynamic` option is enabled. 

```
 
```

 The system determines the current appearance using: 
 
- `SystemAppearance.current`: Detects if the system is in light or dark mode

- Based on the current mode, applies either `lightModeConfiguration` or `darkModeConfiguration`

- Automatically switches when the system appearance changes

 
 Sources: Ice/Utilities/SystemAppearance.swift 1-84 Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 62-76 Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 218-271 

## Implementation Details 

### Configuration Persistence 

 The appearance configuration is persisted using `UserDefaults`: 

```
 
```

### Overlay Panel Updates 

 Overlay panels watch for multiple system events to update appropriately: 
 
- Screen Parameter Changes : Re-create panels when screens change

- Space Changes : Update when switching between spaces

- Appearance Changes : Update when switching between light/dark mode

- Menu Bar Visibility : Disable when menu bar is hidden by system

 

### UI Organization 

 The appearance editor UI uses several SwiftUI components: 
 
- `IceSection`: Groups related controls with optional dividers

- `IceGroupBox`: Creates visually distinct groupings with borders

- `LabeledPartialEditor`: Editor for light or dark mode configuration

- `UnlabeledPartialEditor`: Core editor controls without appearance labels

- `PreviewButton`: Temporary preview of a configuration

 
 Sources: Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 77-102 Ice/MenuBar/Appearance/MenuBarOverlayPanel.swift 100-246 Ice/UI/IceUI/IceSection.swift 1-137 Ice/UI/IceUI/IceGroupBox.swift 1-104 

## Limitations 

 The menu bar appearance system has some limitations: 
 
- Cannot modify automatically hidden menu bars (configured in System Settings)

- Limited to visual customization (cannot add or remove menu bar items)

- Customization must be applied across all screens (though each screen gets its own panel)

- Performance impact when using complex shapes with borders and shadows

 
 Sources: Ice/MenuBar/Appearance/MenuBarAppearanceEditor/MenuBarAppearanceEditor.swift 52-56 Ice/MenuBar/Appearance/MenuBarAppearanceManager.swift 109-123 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Menu Bar Appearance 
- System Overview 
- Key Components 
- Configuration Model 
- Overlay Panel Mechanism 
- Shape Rendering Process 
- User Interface 
- Dynamic Appearance 
- Implementation Details 
- Configuration Persistence 
- Overlay Panel Updates 
- UI Organization 
- Limitations

---

_DeepWiki section: 4.2-gradient-and-color-selection_

Relevant source files 
 
- Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 

- Ice/UI/ViewModifiers/LocalEventMonitorModifier.swift 

- Ice/UI/ViewModifiers/OnKeyDown.swift 

- Ice/UI/Views/SectionedList.swift 

 
 
 The Gradient and Color Selection system in Ice provides the tools and user interface components for creating and customizing gradients throughout the application. This system is primarily used within the menu bar appearance customization settings, allowing users to create complex multi-stop gradients with precise control over colors and positions. 
 For information about how these gradients are applied to the menu bar's appearance, see Menu Bar Appearance . 

## 1. CustomGradientPicker Component 

 The core of the gradient and color selection system is the `CustomGradientPicker` SwiftUI component, which provides an interactive gradient editor. 

```
 
```

 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 9-41 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 205-235 

### 1.1 Component Configuration 

 The `CustomGradientPicker` is initialized with several parameters that control its behavior: 

 Parameter Type Purpose `gradient` `Binding<CustomGradient>` Binding to the gradient being edited `supportsOpacity` `Bool` Whether opacity adjustments are allowed `allowsEmptySelections` `Bool` Whether gradients with no stops are permitted `mode` `NSColorPanel.Mode` The mode for the system color picker 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 21-41 

### 1.2 Visual Structure 

 The `CustomGradientPicker` combines several visual components: 

```
 
```

 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 43-64 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 66-97 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 144-158 

## 2. User Interaction Flow 

 The gradient picker implements a comprehensive set of interactions for gradient customization. 

```
 
```

 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 123-142 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 253-299 

### 2.1 Adding Color Stops 

 Users can add color stops by clicking on empty areas of the gradient preview: 
 
- The `insertionReader` captures the click location using a `DragGesture`

- The `insertStop(at:select:)` method calculates the normalized position (0-1)

- A new `ColorStop` is created with an appropriate color and added to the gradient

- The new stop is selected for immediate editing

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 123-142 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 160-181 

### 2.2 Selecting and Editing Colors 

 When a color stop is selected: 
 
- The macOS system `NSColorPanel` is opened via the `activate()` method

- The panel is configured based on the `supportsOpacity` and `mode` settings

- Combine publishers monitor color changes in the panel

- Changes are immediately applied to the selected stop

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 352-399 

### 2.3 Repositioning Color Stops 

 Color stops can be repositioned by dragging their handles: 
 
- A `DragGesture` in the handle captures drag movement

- The `update(with:shouldSnap:)` method calculates the new position

- Positions near 0.5 (center) snap to exactly 0.5 when moving slowly

- The updated position is applied to the stop in real-time

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 253-267 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 328-350 

### 2.4 Special Operations 

 The gradient picker supports several specialized operations: 

 Operation Gesture/Event Implementation Effect Delete Stop Delete key `onKeyDown` modifier Removes the selected stop Even Distribution Double-click `onTapGesture(count: 2)` Distributes stops evenly across gradient Deselection Click outside handles `localEventMonitor` Clears selection 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 268-299 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 410-420 Ice/UI/ViewModifiers/OnKeyDown.swift 8-20 

## 3. Technical Implementation Details 

### 3.1 Event Handling System 

 The component uses several SwiftUI extensions for event handling: 

 Component File Purpose `localEventMonitor` Ice/UI/ViewModifiers/LocalEventMonitorModifier.swift Low-level event monitoring `onKeyDown` Ice/UI/ViewModifiers/OnKeyDown.swift Keyboard event handling 
 The `localEventMonitor` modifier creates a `LocalEventMonitor` instance that captures system events before they're processed by the application. This enables the component to detect clicks outside its bounds and implement deselection behavior. 
 Sources: Ice/UI/ViewModifiers/LocalEventMonitorModifier.swift 8-48 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 99-120 

### 3.2 NSColorPanel Integration 

 The `CustomGradientPickerHandle` integrates with the macOS system color panel: 

```
 
```

 This integration: 
 
- Configures the color panel based on component settings

- Establishes Combine publishers to observe color changes

- Updates the gradient in real-time as colors are modified

- Manages the panel's visibility and position

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 352-399 

### 3.3 Z-Order Management 

 The component maintains a `zOrderedStops` array to track the visual stacking order of handles: 
 
- When a handle is selected, it's moved to the end of the array (highest z-index)

- The `zIndex` modifier applies this ordering to the handle views

- This ensures that when handles overlap, the active handle remains accessible

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 285 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 366-368 

## 4. Gradient Consistency Management 

 The component includes mechanisms to ensure valid gradient configurations. 

### 4.1 Empty Gradient Prevention 

 When `allowsEmptySelections` is `false`, the `gradientChanged` method maintains gradient validity: 

```
 
```

 This method: 
 
- Replaces empty gradients with a default gradient

- Adds a complementary stop when only one exists

- Positions the new stop at the opposite end of the gradient

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 183-202 

### 4.2 Location Snapping 

 The component implements position snapping to help users create precise gradients: 
 
- In the `insertStop` method, locations near 0.5 snap exactly to 0.5

- In the `update` method, slow drag operations near 0.5 also snap to center

- This helps users create symmetric gradients with precisely centered stops

 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 160-181 Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 328-350 

## 5. Integration with Menu Bar Appearance 

 The `CustomGradientPicker` component is primarily used in the Menu Bar Appearance settings to customize the menu bar's visual style. It enables users to: 
 
- Create complex gradients for the menu bar background

- Precisely position and color multiple gradient stops

- Preview the gradient effect in real-time

 
 By combining this component with other appearance controls, Ice provides comprehensive customization of the menu bar's visual appearance. 
 Sources: Ice/UI/Pickers/CustomGradientPicker/CustomGradientPicker.swift 9-203 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Gradient and Color Selection 
- 1. CustomGradientPicker Component 
- 1.1 Component Configuration 
- 1.2 Visual Structure 
- 2. User Interaction Flow 
- 2.1 Adding Color Stops 
- 2.2 Selecting and Editing Colors 
- 2.3 Repositioning Color Stops 
- 2.4 Special Operations 
- 3. Technical Implementation Details 
- 3.1 Event Handling System 
- 3.2 NSColorPanel Integration 
- 3.3 Z-Order Management 
- 4. Gradient Consistency Management 
- 4.1 Empty Gradient Prevention 
- 4.2 Location Snapping 
- 5. Integration with Menu Bar Appearance

---

_DeepWiki section: 5-settings-system_

Relevant source files 
 
- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 

- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 

- Ice/Settings/SettingsPanes/AboutSettingsPane.swift 

- Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 

- Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 

- Ice/Settings/SettingsView.swift 

- Ice/Settings/SettingsWindow.swift 

- Ice/Utilities/Defaults.swift 

 
 
 The Settings System in Ice provides a framework for managing user preferences, persisting configuration to disk, and presenting a user interface for settings modification. This document covers the architecture and implementation of the settings system itself, not specific settings categories. 
 For details about specific settings categories, see: 
 
- General Settings 

- Advanced Settings 

- Hotkeys 

- Menu Bar Layout 

- Settings Window 

 

## System Architecture 

 The Settings System follows an MVVM (Model-View-ViewModel) architecture with UserDefaults as the underlying persistence layer. It consists of these core components: 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 

- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 

- Ice/Utilities/Defaults.swift 

 
 Each settings category follows this pattern, with specific panes (views) backed by dedicated manager classes (view models) that handle persistence and business logic. 

## Key Components 

### Settings Managers 

 Settings managers serve as view models for their respective settings panes. They handle: 
 
- Loading initial state from persistent storage

- Defining reactive bindings between UI and storage

- Processing settings changes

- Communicating settings to other app systems

 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 9-222 

- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 9-119 

 

### Settings Panes 

 Settings panes are SwiftUI views that present user interfaces for their respective settings categories. They: 
 
- Access managers through environment objects

- Bind UI controls to manager properties

- Present settings in logical groupings

- Provide annotations and help text

 
 Sources: 
 
- Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 9-340 

- Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 8-169 

 

### Defaults System 

 The `Defaults` class provides type-safe access to UserDefaults storage with specialized functions for each data type: 

```
 
```

 Sources: 
 
- Ice/Utilities/Defaults.swift 8-136 

- Ice/Utilities/Defaults.swift 138-200 

 

## Settings Lifecycle 

 The settings system follows this initialization and data flow pattern: 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 74-115 

- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 43-60 

 

## Data Binding Architecture 

 The Settings System uses Combine for reactive data binding between UI, managers, and storage: 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 117-217 

- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 62-115 

 
 This architecture ensures that: 
 
- Changes to UI controls are immediately reflected in manager properties

- Changes to manager properties are persisted to UserDefaults

- Changes to manager properties trigger updates to app functionality

- Settings are loaded from persistent storage at app launch

 

## Settings Keys 

 The Settings System uses an enumeration of keys to provide type safety and organization. Keys are grouped by category: 

 Category Example Keys Purpose General Settings `showIceIcon`, `useIceBar`, `showOnClick` Basic user interaction preferences Advanced Settings `hideApplicationMenus`, `showSectionDividers`, `tempShowInterval` Power user and technical options Hotkey Settings `hotkeys` Keyboard shortcut configuration Menu Bar Appearance `menuBarAppearanceConfigurationV2` Visual customization options Ice Bar Settings `iceBarLocation`, `iceBarPinnedLocation` Ice Bar positioning and behavior Migration `hasMigrated0_8_0`, `hasMigrated0_10_0` Version migration tracking 
 Sources: 
 
- Ice/Utilities/Defaults.swift 141-184 

 

## Integration with AppState 

 Settings managers are accessible throughout the app via the central `AppState` object: 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 10 

- Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 9 

 
 This pattern allows any component that has access to AppState to read or modify settings, while maintaining a centralized approach to settings management. 

## Settings UI Structure 

 The Settings UI is structured as a Navigation Split View with a sidebar for category selection and detail views for specific settings: 

```
 
```

 Sources: 
 
- Ice/Settings/SettingsWindow.swift 8-28 

- Ice/Settings/SettingsView.swift 8-111 

 

## Summary 

 The Settings System in Ice follows modern architectural patterns with: 
 
- Clean separation of UI (settings panes) from business logic (settings managers)

- Type-safe storage through a custom `Defaults` wrapper

- Reactive data binding using Combine

- Centralized access through `AppState`

- Organized UI using SwiftUI's Navigation architecture

 
 This design provides a flexible, maintainable system for managing application preferences while ensuring consistent state across app restarts. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Settings System 
- System Architecture 
- Key Components 
- Settings Managers 
- Settings Panes 
- Defaults System 
- Settings Lifecycle 
- Data Binding Architecture 
- Settings Keys 
- Integration with AppState 
- Settings UI Structure 
- Summary

---

_DeepWiki section: 5.1-general-settings_

Relevant source files 
 
- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 

- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 

- Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 

- Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 

- Ice/Utilities/Defaults.swift 

 
 
 This document details the General Settings system in Ice, which manages the core user preferences that control Ice's basic behavior and appearance. General settings cover interface elements like the Ice icon, menu bar interactions, Ice Bar options, and auto-rehiding behavior. 
 For information about advanced settings like section dividers and permission management, see Advanced Settings . For appearance customization settings, see Menu Bar Appearance . 

## Overview and Architecture 

 General settings in Ice are managed through the `GeneralSettingsManager` class, which maintains state for user preferences and handles saving them to persistent storage. This manager is part of the broader settings system and integrates with the central application state. 

```
 
```

 Sources: Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 9-218 Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 9-86 

## Settings Categories 

 The General Settings system is organized into several core categories that control different aspects of Ice's behavior: 

```
 
```

 Sources: Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 13-60 

## Ice Icon Settings 

 These settings control the visibility and appearance of the Ice icon in the menu bar. 

 Setting Type Description Default `showIceIcon` Boolean Whether to show the Ice icon in the menu bar `true` `iceIcon` ControlItemImageSet The image to use for the Ice icon Default Ice icon `customIceIconIsTemplate` Boolean Whether to apply the system theme to custom icons `false` 
 The Ice icon serves as the main entry point to the application for users. If disabled, users can still access settings by right-clicking an empty area in the menu bar. 
 Users can customize the Ice icon by selecting from built-in options or importing a custom image. When a custom icon is used, users can also choose whether it should be displayed as a template image (monochrome that matches the system appearance). 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 116-162 Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 13-24 

## Menu Bar Interaction Settings 

 These settings determine how users interact with the menu bar to show or hide menu bar items. 

 Setting Type Description Default `showOnClick` Boolean Show hidden items when clicking empty area `true` `showOnHover` Boolean Show hidden items when hovering over empty area `false` `showOnScroll` Boolean Toggle hidden items when scrolling in menu bar `true` 
 These interaction methods provide flexibility for users to access hidden menu bar items according to their preference. The hover sensitivity can be fine-tuned in Advanced Settings with the `showOnHoverDelay` setting. 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 197-213 Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 36-47 

## Ice Bar Settings 

 The Ice Bar is an alternative interface for displaying hidden menu bar items in a separate bar. 

 Setting Type Description Default `useIceBar` Boolean Show hidden items in a separate bar `false` `iceBarLocation` IceBarLocation Where to position the Ice Bar `.dynamic` 
 The Ice Bar location can be set to one of three values: 
 
- `dynamic`: The Ice Bar's location changes based on context

- `mousePointer`: The Ice Bar is centered below the mouse pointer

- `iceIcon`: The Ice Bar is centered below the Ice icon

 
 These settings allow users to customize how they access hidden menu bar items, particularly when they prefer not to have them temporarily appear in the menu bar itself. 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 164-195 Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 28-31 

## Auto-Rehide Settings 

 These settings control how and when hidden menu bar items automatically hide again after being shown. 

 Setting Type Description Default `autoRehide` Boolean Automatically rehide shown items `true` `rehideStrategy` RehideStrategy Method used to determine when to rehide `.smart` `rehideInterval` TimeInterval Time before rehiding (for timed strategy) `15` seconds 
 The `rehideStrategy` can be one of: 
 
- `smart`: Uses a smart algorithm to determine when to rehide

- `timed`: Rehides after a fixed interval (specified by `rehideInterval`)

- `focusedApp`: Rehides when the focused application changes

 
 These settings allow users to control the behavior of temporarily shown menu bar items to suit their workflow preferences. 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 293-311 Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 53-60 

## Menu Bar Spacing Settings 

 Ice provides the ability to adjust the spacing between menu bar items, which is useful for users who want more or less dense menu bars. 

 Setting Type Description Default `itemSpacingOffset` Double Offset to apply to menu bar item spacing `0` (default spacing) 
 The spacing can be adjusted with values from -16 (none) to 16 (maximum), with 0 representing the system default. This feature is marked as beta and requires restarting applications or potentially logging out to fully apply. 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 215-269 Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 49 

## Settings Persistence Flow 

 General settings follow a consistent pattern for persistence, using UserDefaults as the storage mechanism. The diagram below shows how settings changes flow from UI to persistence: 

```
 
```

 Sources: Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 77-115 Ice/Utilities/Defaults.swift 1-136 

## Implementation Details 

### Initialization and Setup 

 The `GeneralSettingsManager` initializes settings from `UserDefaults` on startup through the `performSetup()` method, which: 
 
- Loads initial values from storage with `loadInitialState()`

- Sets up Combine publishers to persist changes with `configureCancellables()`

 
 This ensures settings persist between application launches and update immediately when changed. 
 Sources: Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 77-118 

### UI Components 

 The `GeneralSettingsPane` provides the user interface for modifying general settings, organized into logical sections: 
 
- Launch at login toggle

- Ice icon selection and customization

- Ice Bar options and location

- Menu bar interaction toggles (click, hover, scroll)

- Auto-rehide options

- Menu bar spacing controls

 
 Each setting includes appropriate annotations (help text) to explain its function to users. 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 55-77 

### Special Components 

 Some settings require special handling: 
 
- 
 Custom Ice Icons : Users can import custom images, which are encoded and stored in UserDefaults. 

- 
 Menu Bar Spacing : This requires a two-step process where users: 
 
 Adjust a slider to preview the spacing

- Click "Apply" to implement the change, which relaunches applications

 
 
- 
 IceBarLocation : Uses an enum selector with visual feedback showing the impact of each choice. 

 
 Sources: Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 116-162 Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 215-269 Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 179-195 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - General Settings 
- Overview and Architecture 
- Settings Categories 
- Ice Icon Settings 
- Menu Bar Interaction Settings 
- Ice Bar Settings 
- Auto-Rehide Settings 
- Menu Bar Spacing Settings 
- Settings Persistence Flow 
- Implementation Details 
- Initialization and Setup 
- UI Components 
- Special Components

---

_DeepWiki section: 5.2-advanced-settings_

Relevant source files 
 
- Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 

- Ice/Settings/SettingsManagers/GeneralSettingsManager.swift 

- Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 

- Ice/Settings/SettingsPanes/GeneralSettingsPane.swift 

- Ice/Utilities/Defaults.swift 

 
 

## Purpose and Scope 

 The Advanced Settings system in Ice provides configuration options for power users who need finer control over the application's behavior. This page documents the Advanced Settings subsystem, which manages specialized configurations affecting menu bar sections, timing behaviors, and permissions. For basic configuration options, see General Settings . 

## System Architecture 

 The Advanced Settings system follows the same architectural pattern as other settings systems in Ice, using an MVVM approach with a manager class that handles the business logic and a UI pane that presents the settings to the user. 

```
 
```

 Sources: Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 

## Core Components 

### AdvancedSettingsManager 

 The `AdvancedSettingsManager` class is responsible for: 
 
- Managing advanced setting properties

- Persisting settings to UserDefaults

- Notifying the system of setting changes

 

```
 
```

 Sources: Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 10-116 

### Available Settings 

 The Advanced Settings system manages the following configuration options: 

 Setting Default Value Description `hideApplicationMenus` `true` Hides application menus when showing menu bar items to make more room `showSectionDividers` `false` Inserts divider items between menu bar sections `enableAlwaysHiddenSection` `false` Enables the always-hidden section for menu bar items `canToggleAlwaysHiddenSection` `true` Allows the always-hidden section to be toggled with Option key `showOnHoverDelay` `0.2` seconds Delay before showing menu bar items on hover `tempShowInterval` `15` seconds Delay before rehiding temporarily shown items `showAllSectionsOnUserDrag` `true` Shows all sections when Command + dragging menu bar items 
 Sources: Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 10-36 Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 29-47 

## UI Implementation 

 The `AdvancedSettingsPane` is a SwiftUI view that presents the advanced settings to the user. It's organized into several sections: 
 
- Menu Bar Behavior Settings

- Always-Hidden Section Settings

- Timing Settings

- Permissions Settings

 

```
 
```

 Sources: Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 29-162 

## Settings Persistence 

 All advanced settings are stored in the UserDefaults database using a dedicated set of keys defined in the `Defaults.Key` enum: 

```
 
```

 Sources: Ice/Utilities/Defaults.swift 161-167 Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 62-114 

## Menu Bar Behavior Settings 

### Hide Application Menus 

 When enabled, this setting allows Ice to hide application menus on the left side of the menu bar when showing hidden menu bar items. This creates more space for displaying items. 

### Show Section Dividers 

 When enabled, this setting inserts divider items (chevrons) between menu bar sections to visually separate different groups of menu bar items. 

### Show All Sections on User Drag 

 When enabled, this setting automatically shows all menu bar sections when the user is Command + dragging menu bar items, making it easier to rearrange items between sections. 
 Sources: Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 50-76 Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 136-139 

## Always-Hidden Section Settings 

### Enable Always-Hidden Section 

 This setting enables a special "always-hidden" section that contains menu bar items that are permanently hidden from the menu bar. These items can only be accessed through the Ice Bar or when explicitly showing the always-hidden section. 

### Always-Hidden Section Can Be Shown 

 When enabled, this setting allows users to temporarily show the always-hidden section by Option + clicking either one of Ice's menu bar items or an empty area in the menu bar (if "Show on click" is enabled in General Settings). 
 Sources: Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 77-94 

## Timing Settings 

### Show on Hover Delay 

 This slider setting controls the delay (in seconds) before showing menu bar items when the mouse hovers over an empty area of the menu bar. The default is 0.2 seconds, and it can be adjusted from 0 to 1 second in 0.1-second increments. 

### Temporarily Shown Item Delay 

 This slider setting controls how long (in seconds) temporarily shown menu bar items remain visible before being automatically hidden. The default is 15 seconds, and it can be adjusted from 0 to 30 seconds. 
 Sources: Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 96-134 

## Permissions Settings 

 The Advanced Settings pane also provides direct access to Ice's permission requirements: 
 
- Accessibility Permission - Allows Ice to control and modify the menu bar

- Screen Recording Permission - Allows Ice to capture menu bar item images

 
 The UI shows the current permission status and provides buttons to request missing permissions. 
 Sources: Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift 141-162 

## Data Flow and State Management 

 Advanced settings use Combine framework publishers and subscribers to maintain reactivity throughout the system. When a setting is changed: 
 
- The UI updates the corresponding `@Published` property in `AdvancedSettingsManager`

- Combine observers detect the change and persist it to UserDefaults

- Other system components observe these changes and react accordingly

 

```
 
```

 Sources: Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift 62-114 

## Integration with Other Systems 

 The Advanced Settings are closely integrated with several other systems in Ice: 

### Menu Bar Section Management 

 The always-hidden section settings directly affect how the menu bar organizes and displays items. When `enableAlwaysHiddenSection` is turned on, a special section is created to hold permanently hidden items. 

### Event Handling 

 The timing settings (`showOnHoverDelay` and `tempShowInterval`) affect how the event handling system responds to user interactions with the menu bar. 

### Menu Bar Appearance 

 Settings like `showSectionDividers` directly affect the visual appearance of the menu bar by inserting divider items between sections. 
 Sources: Ice/Settings/SettingsManagers/AdvancedSettingsManager.swift Ice/Settings/SettingsPanes/AdvancedSettingsPane.swift Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Advanced Settings 
- Purpose and Scope 
- System Architecture 
- Core Components 
- AdvancedSettingsManager 
- Available Settings 
- UI Implementation 
- Settings Persistence 
- Menu Bar Behavior Settings 
- Hide Application Menus 
- Show Section Dividers 
- Show All Sections on User Drag 
- Always-Hidden Section Settings 
- Enable Always-Hidden Section 
- Always-Hidden Section Can Be Shown 
- Timing Settings 
- Show on Hover Delay 
- Temporarily Shown Item Delay 
- Permissions Settings 
- Data Flow and State Management 
- Integration with Other Systems 
- Menu Bar Section Management 
- Event Handling 
- Menu Bar Appearance

---

_DeepWiki section: 5.3-hotkeys_

Relevant source files 
 
- Ice/Hotkeys/HotkeyAction.swift 

- Ice/Hotkeys/HotkeyRegistry.swift 

- Ice/Hotkeys/KeyCombination.swift 

- Ice/Settings/SettingsManagers/HotkeySettingsManager.swift 

- Ice/Settings/SettingsManagers/SettingsManager.swift 

- Ice/Settings/SettingsPanes/HotkeysSettingsPane.swift 

 
 

## Purpose and Scope 

 This document covers the hotkey system in Ice, which allows users to assign keyboard shortcuts to various actions within the application. The hotkey system enables users to efficiently control menu bar sections, search menu bar items, and toggle various features without using the mouse. 
 The document explains the architecture of the hotkey system, available actions, the registration process, user configuration, and technical implementation details. 
 Sources: Ice/Hotkeys/HotkeyAction.swift 6-50 

## Hotkey System Architecture 

 The hotkey system in Ice consists of several interconnected components that handle different aspects of hotkey functionality, from user configuration to system-level registration and event handling. 

### System Components Diagram 

```
 
```

 Sources: Ice/Settings/SettingsManagers/HotkeySettingsManager.swift 9-87 Ice/Hotkeys/HotkeyRegistry.swift 10-286 Ice/Hotkeys/HotkeyAction.swift 6-50 

### Hotkey System Flow 

```
 
```

 Sources: Ice/Settings/SettingsManagers/HotkeySettingsManager.swift 32-81 Ice/Hotkeys/HotkeyRegistry.swift 122-182 

## Available Hotkey Actions 

 Ice provides several predefined actions that can be assigned to hotkeys, organized into three categories: 

### Menu Bar Sections 

 
- Toggle Hidden Section : Shows or hides the "hidden" section of menu bar items

- Toggle Always-Hidden Section : Shows or hides the "always-hidden" section of menu bar items

 

### Menu Bar Items 

 
- Search Menu Bar Items : Opens the menu bar search interface

 

### Other 

 
- Enable Ice Bar : Toggles the Ice Bar feature

- Show Section Dividers : Toggles visibility of section dividers in the menu bar

- Toggle Application Menus : Shows or hides application menus

 
 Each action is defined as a case in the `HotkeyAction` enum, which also contains the implementation for performing the action when triggered. 

```
 
```

 Sources: Ice/Hotkeys/HotkeyAction.swift 6-50 Ice/Settings/SettingsPanes/HotkeysSettingsPane.swift 17-28 

## Hotkey Registration Process 

 The registration process connects a user-defined key combination with a specific action. This involves several steps and components: 
 
- Key Combination Definition : Users define a key combination through the settings UI

- Storage : The combination is stored in UserDefaults

- System Registration : The combination is registered with macOS using Carbon APIs

- Event Handling : When triggered, the associated action is performed

 

### Registration Flow 

```
 
```

 The `HotkeyRegistry` class handles the low-level interaction with macOS's Carbon API for hotkey registration, ensuring that: 
 
- Hotkeys can be registered and unregistered dynamically

- Hotkeys are temporarily disabled when menus are being tracked

- System-reserved hotkeys are detected to avoid conflicts

 
 Sources: Ice/Hotkeys/HotkeyRegistry.swift 122-185 Ice/Hotkeys/HotkeyRegistry.swift 247-285 

## Key Combination Handling 

 A key combination consists of a key code and modifier flags. The `KeyCombination` struct encapsulates this information and provides utility methods. 

### Features of KeyCombination 

 
- Represents keyboard combinations (key + modifiers)

- Provides string representation for UI display

- Checks if a combination is reserved by the system

- Supports encoding/decoding for persistence

 

### System Reserved Combinations 

 The `isReservedBySystem` property checks if a key combination is already used by macOS. This prevents users from assigning hotkeys that would conflict with system functions, ensuring a better user experience. 

```
 
```

 Sources: Ice/Hotkeys/KeyCombination.swift 9-90 

## User Configuration Interface 

 The hotkey settings interface allows users to customize keyboard shortcuts for various actions. This is implemented in the `HotkeysSettingsPane` view. 

### Interface Organization 

 The settings pane organizes hotkey configuration into three main sections: 
 
- Menu Bar Sections - Contains toggles for hidden and always-hidden sections

- Menu Bar Items - Contains the search menu bar items action

- Other - Contains various additional actions like enabling the Ice Bar

 
 Each action is represented by a `HotkeyRecorder` control that shows the current key combination and allows recording a new one. 

```
 
```

 Sources: Ice/Settings/SettingsPanes/HotkeysSettingsPane.swift 8-64 

## Technical Implementation Details 

 The hotkey system in Ice is built on several technical foundations: 

### Key Components 

 Component Role `HotkeyRegistry` Manages registration of hotkeys with the system using Carbon APIs `HotkeySettingsManager` Manages persistence and configuration of hotkeys `HotkeyAction` Defines available actions and their implementations `KeyCombination` Represents a key + modifier combination `Hotkey` Connects a key combination with an action 

### Carbon API Integration 

 The hotkey system uses macOS's Carbon APIs for low-level hotkey registration: 
 
- `InstallEventHandler` - Sets up the event handler for hotkey events

- `RegisterEventHotKey` - Registers a specific key combination with the system

- `GetEventParameter` - Extracts information from hotkey events

- `UnregisterEventHotKey` - Removes a hotkey registration

 

### Event Flow 

 
- User presses a registered key combination

- System generates a Carbon event

- Event is received by the installed event handler

- Event is matched to a registered hotkey

- Associated action is performed

 

### Menu Tracking Handling 

 The `HotkeyRegistry` temporarily unregisters hotkeys when menus are being tracked, to prevent hotkeys from interfering with menu navigation. This is done by: 
 
- Observing `NSMenu.didBeginTrackingNotification` and `NSMenu.didEndTrackingNotification`

- Unregistering hotkeys when menu tracking begins

- Re-registering hotkeys when menu tracking ends

 
 Sources: Ice/Hotkeys/HotkeyRegistry.swift 63-106 Ice/Hotkeys/HotkeyRegistry.swift 198-245 

## Integration with Other Systems 

 The hotkey system integrates with other parts of Ice: 

### Menu Bar Management 

 Hotkeys can control the visibility of menu bar sections, allowing users to show or hide groups of menu bar items. 

### Ice Bar 

 A hotkey can toggle the Ice Bar feature, providing keyboard access to this alternative interface. 

### Settings System 

 Hotkey configurations are managed through the settings system, which persists them using UserDefaults. 
 Sources: Ice/Hotkeys/HotkeyAction.swift 19-49 Ice/Settings/SettingsManagers/SettingsManager.swift 16-17 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Hotkeys 
- Purpose and Scope 
- Hotkey System Architecture 
- System Components Diagram 
- Hotkey System Flow 
- Available Hotkey Actions 
- Menu Bar Sections 
- Menu Bar Items 
- Other 
- Hotkey Registration Process 
- Registration Flow 
- Key Combination Handling 
- Features of KeyCombination 
- System Reserved Combinations 
- User Configuration Interface 
- Interface Organization 
- Technical Implementation Details 
- Key Components 
- Carbon API Integration 
- Event Flow 
- Menu Tracking Handling 
- Integration with Other Systems 
- Menu Bar Management 
- Ice Bar 
- Settings System

---

_DeepWiki section: 5.4-menu-bar-layout_

Relevant source files 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 

- Ice/MenuBar/Spacing/MenuBarItemSpacingManager.swift 

- Ice/UI/LayoutBar/LayoutBarContainer.swift 

- Ice/UI/LayoutBar/LayoutBarItemView.swift 

- Ice/UI/LayoutBar/LayoutBarPaddingView.swift 

- Ice/UI/LayoutBar/LayoutBarScrollView.swift 

- Ice/Utilities/Logging.swift 

 
 
 This document describes the Menu Bar Layout system in Ice, which allows users to visually arrange and organize their menu bar items. The system provides an interface for viewing, reordering, and managing the spacing of menu bar items across different sections. 
 For information about the general settings for the menu bar, see General Settings . For details about the menu bar sections themselves, see Sections and Items . 

## Overview of the Layout System 

 The Menu Bar Layout system provides a visual representation of menu bar items organized by section, allowing users to: 
 
- View all menu bar items in their respective sections

- Drag and drop items to reorder them

- Control spacing between items

- Visualize hidden and visible items

 
 The system works by creating visual representations of the actual menu bar items and syncing any changes back to the real menu bar. 

```
 
```

 Sources: Ice/UI/LayoutBar/LayoutBarScrollView.swift 1-94 Ice/UI/LayoutBar/LayoutBarPaddingView.swift 1-149 Ice/UI/LayoutBar/LayoutBarContainer.swift 1-303 Ice/UI/LayoutBar/LayoutBarItemView.swift 1-233 

## Layout Component Hierarchy 

 The Menu Bar Layout interface consists of several nested components that work together to provide a scrollable, interactive representation of menu bar items. 

```
 
```

 Each level has a specific responsibility: 
 
- LayoutBarScrollView : Provides scrolling capabilities and contains the padding view

- LayoutBarPaddingView : Manages drag and drop events and contains the container

- LayoutBarContainer : Handles the arrangement of item views and their animations

- LayoutBarItemView : Represents an individual menu bar item with its image

 
 Sources: Ice/UI/LayoutBar/LayoutBarScrollView.swift 8-94 Ice/UI/LayoutBar/LayoutBarPaddingView.swift 10-93 Ice/UI/LayoutBar/LayoutBarContainer.swift 10-302 Ice/UI/LayoutBar/LayoutBarItemView.swift 12-178 

## Item Representation and Display 

 Each menu bar item is represented by a `LayoutBarItemView` that displays the item's image and provides drag functionality. 

### LayoutBarItemView Features 

 
- Displays the menu bar item's icon image

- Shows a warning icon for unresponsive items

- Dims disabled items that cannot be moved

- Provides tooltips with the item's name

- Supports drag and drop for reordering

 
 The view uses the `MenuBarItemImageCache` to retrieve images for each menu bar item, ensuring the visual representation matches the actual menu bar item. 

```
 
```

 Sources: Ice/UI/LayoutBar/LayoutBarItemView.swift 12-233 

## Arranging Items and Layout 

 The `LayoutBarContainer` is responsible for arranging the item views from left to right with proper spacing. It handles the following: 
 
- Calculating the position of each item view

- Animating item movements during reordering

- Updating the container's size based on its contents

- Responding to changes in the ItemCache

 

```
 
```

 The container uses auto layout constraints to position itself within the padding view: 

```
 
```

 Sources: Ice/UI/LayoutBar/LayoutBarContainer.swift 115-180 Ice/UI/LayoutBar/LayoutBarPaddingView.swift 47-59 

## Drag and Drop System 

 The Menu Bar Layout system provides a complete drag and drop interface for reordering menu bar items. This is implemented through a multi-phase process: 

### Drag and Drop Phases 

 
- Initiated : When a user starts dragging an item view

- Entered : When a dragged item enters a layout container

- Updated : When a dragged item moves within a container

- Exited : When a dragged item leaves a container

- Ended : When the user releases the dragged item

 

```
 
```

 The system keeps track of the item's original container and position through the `oldContainerInfo` property, allowing it to be returned if the drop operation is canceled or fails. 
 Sources: Ice/UI/LayoutBar/LayoutBarPaddingView.swift 69-142 Ice/UI/LayoutBar/LayoutBarContainer.swift 217-302 Ice/UI/LayoutBar/LayoutBarItemView.swift 152-225 

## Item Movement Implementation 

 When an item is dropped in a new position, the system initiates an actual movement of the corresponding menu bar item using the `MenuBarItemManager`. This involves: 
 
- Determining the target destination based on adjacent items

- Creating a `MoveDestination` specifying the target position

- Calling `slowMove` on the `MenuBarItemManager` to physically move the item in the menu bar

 
 The movement destinations are defined as: 

```
 
```

 When a layout operation is performed, it's translated into actual menu bar item movement: 

```
 
```

 Sources: Ice/UI/LayoutBar/LayoutBarPaddingView.swift 127-142 Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 565-631 

## Spacing Management 

 The `MenuBarItemSpacingManager` provides control over the spacing between menu bar items by interfacing with macOS system settings. 

### Key Features: 

 
- Adjusts system-wide menu bar item spacing

- Modifies both spacing and padding values for items

- Requires restarting affected applications to apply changes

 

```
 
```

 When spacing changes are applied, the system: 
 
- Writes new values to system defaults

- Restarts applications with menu bar items to apply the changes

- Handles any failures in restarting applications

 
 Sources: Ice/MenuBar/Spacing/MenuBarItemSpacingManager.swift 10-215 

## Integration with Menu Bar Item Management 

 The layout system integrates with the `MenuBarItemManager` to: 
 
- Retrieve current menu bar items through the `ItemCache`

- Move items to new positions in response to drag operations

- Update the visual layout when items are moved through other means

 
 The `MenuBarItemManager` maintains a cache of all menu bar items organized by section (visible, hidden, always hidden), which the layout system uses to populate its views. 

```
 
```

 The `MenuBarItemManager` handles the complex task of physically moving menu bar items through low-level event simulation, while the layout system provides the user interface for initiating these moves. 
 Sources: Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift 10-225 Ice/UI/LayoutBar/LayoutBarContainer.swift 90-100 

## Error Handling and Feedback 

 The layout system provides feedback to users when operations fail: 
 
- Disabling items that cannot be moved and showing an alert when attempted

- Displaying warning indicators for unresponsive applications

- Showing error dialogs when movements fail

- Automatically returning items to their original positions when operations fail

 

```
 
```

 Sources: Ice/UI/LayoutBar/LayoutBarItemView.swift 109-122 Ice/UI/LayoutBar/LayoutBarPaddingView.swift 133-141 

## Summary 

 The Menu Bar Layout system provides a user-friendly interface for viewing and rearranging menu bar items. It uses a component hierarchy of scroll view, padding view, container, and item views to represent the menu bar's organization. Through drag and drop operations, users can reorder items, which are then physically moved in the system menu bar by the `MenuBarItemManager`. The system also provides controls for adjusting the spacing between menu bar items across the entire system. 
 This layout system forms a crucial part of Ice's user interface, allowing users to easily visualize and manage their menu bar items across different sections. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Menu Bar Layout 
- Overview of the Layout System 
- Layout Component Hierarchy 
- Item Representation and Display 
- LayoutBarItemView Features 
- Arranging Items and Layout 
- Drag and Drop System 
- Drag and Drop Phases 
- Item Movement Implementation 
- Spacing Management 
- Key Features: 
- Integration with Menu Bar Item Management 
- Error Handling and Feedback 
- Summary

---

_DeepWiki section: 5.5-settings-window_

Relevant source files 
 
- Ice/Settings/SettingsPanes/AboutSettingsPane.swift 

- Ice/Settings/SettingsView.swift 

- Ice/Settings/SettingsWindow.swift 

 
 

## Purpose and Overview 

 The Settings Window is the central interface for configuring all aspects of the Ice application. It provides access to various settings categories through a sidebar navigation system and displays the corresponding settings content in a detail view. The window is designed with a consistent layout that allows users to easily navigate between different settings categories while maintaining a cohesive user experience. 
 For information about specific settings options, see General Settings , Advanced Settings , Hotkeys , and Menu Bar Layout . 

## Window Architecture 

 The Settings Window is implemented as a SwiftUI `Scene` that contains a navigation-based view hierarchy. The window has predefined dimensions and utilizes a split view architecture with a sidebar for navigation and a detail area for displaying settings content. 

```
 
```

 Sources: Ice/Settings/SettingsWindow.swift 8-28 Ice/Settings/SettingsView.swift 8-46 

### Key Window Properties 

 The Settings Window is configured with specific dimensions and behaviors: 

 Property Value Description Minimum Width 825 points Ensures adequate space for content Minimum Height 500 points Ensures adequate space for content Default Width 900 points Optimal width for viewing settings Default Height 625 points Optimal height for viewing settings Resizability Content Size Window size is based on content dimensions Commands Removed Standard menu commands are removed 
 Sources: Ice/Settings/SettingsWindow.swift 12-24 

## Navigation System 

 The Settings Window uses SwiftUI's `NavigationSplitView` to provide a sidebar navigation interface. The sidebar displays a list of settings categories represented by `SettingsNavigationIdentifier` values, while the detail area shows the corresponding settings pane. 

```
 
```

 Sources: Ice/Settings/SettingsView.swift 39-46 Ice/Settings/SettingsView.swift 68-86 

### Sidebar Configuration 

 The sidebar is dynamically sized based on the system's sidebar row size setting, which affects both the width of the sidebar and the appearance of sidebar items: 

 Sidebar Row Size Width Item Height Font Size Small 190 points 26 points 13 points Medium 210 points 32 points 15 points Large 230 points 34 points 16 points 
 Each sidebar item consists of a label and an icon, with the icon selected based on the navigation identifier: 

 Navigation Identifier Icon Description .general "gearshape" General settings .menuBarLayout "rectangle.topthird.inset.filled" Menu bar layout settings .menuBarAppearance "swatchpalette" Menu bar appearance settings .hotkeys "keyboard" Hotkey configuration .advanced "gearshape.2" Advanced settings .updates "arrow.triangle.2.circlepath.circle" Update settings .about "iceCubeStroke" (asset catalog) About information 
 Sources: Ice/Settings/SettingsView.swift 12-37 Ice/Settings/SettingsView.swift 88-110 

## Settings Panes 

 The detail view of the Settings Window displays one of seven different settings panes based on the selected navigation identifier. Each pane provides a specific set of settings that the user can configure. 

```
 
```

 Sources: Ice/Settings/SettingsView.swift 68-86 

### Example: About Settings Pane 

 The About Settings Pane displays information about the Ice application, including: 
 
- Application icon

- Application name

- Version number

- Copyright information

- Action buttons (Quit, Acknowledgements, Contribute, Report a Bug, Support)

 
 This pane demonstrates the common layout pattern used across settings panes, with content displayed in the main area and action buttons in a bottom bar. 

```
 
```

 Sources: Ice/Settings/SettingsPanes/AboutSettingsPane.swift 8-92 

## Integration with App State 

 The Settings Window is tightly integrated with the application's state management system. It receives the central `AppState` object as an observed object and provides this state to child views through the environment. 

```
 
```

 The Settings Window and its views interact with two key state objects: 
 
- 
 AppState : The central state object for the entire application, which contains references to various managers. 

- 
 AppNavigationState : A state object specifically for managing navigation within the settings window, tracking which settings pane is currently selected. 

 
 Sources: Ice/Settings/SettingsWindow.swift 8-28 Ice/Settings/SettingsView.swift 8-9 

## Window Lifecycle Management 

 The Settings Window is managed by the `AppState`, which is responsible for opening and closing the window. The window itself notifies the `AppState` of its existence through the `assignSettingsWindow` method called during view initialization. 

```
 
```

 Sources: Ice/Settings/SettingsWindow.swift 14-19 

## Responsive Design 

 The Settings Window implements responsive design principles to ensure a good user experience across different display sizes and system settings: 
 
- The sidebar width and element sizes adapt based on the system sidebar row size setting

- The window has minimum dimensions to ensure content remains usable

- The content within panes (such as the About pane) adapts to the available space

 
 This responsive approach ensures that the Settings Window provides a consistent user experience across different Mac devices and display configurations. 
 Sources: Ice/Settings/SettingsView.swift 12-37 Ice/Settings/SettingsPanes/AboutSettingsPane.swift 31-63 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Settings Window 
- Purpose and Overview 
- Window Architecture 
- Key Window Properties 
- Navigation System 
- Sidebar Configuration 
- Settings Panes 
- Example: About Settings Pane 
- Integration with App State 
- Window Lifecycle Management 
- Responsive Design

---

_DeepWiki section: 6-technical-features_

Relevant source files 
 
- Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV1.swift 

- Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV2.swift 

- Ice/UI/ViewModifiers/LayoutBarStyle.swift 

- Ice/Utilities/MigrationManager.swift 

 
 

## Purpose and Scope 

 This document provides a technical overview of Ice's internal implementation details and architectural features. It covers the core technical mechanisms that power the application, including data migration, appearance configurations, and visual customization features. 
 For specific aspects of menu bar functionality, refer to Menu Bar Features . For settings management, see Settings System . 

## Data Migration System 

 Ice incorporates a robust data migration system to handle configuration changes between versions. The system ensures user settings are properly preserved and updated when upgrading to a new version of the application. 

### Migration Architecture 

 The `MigrationManager` class is the central component responsible for coordinating migrations between different application versions. It uses a structured approach to perform version-specific migrations in sequence. 

```
 
```

 Sources: Ice/Utilities/MigrationManager.swift 17-53 

### Version-Specific Migrations 

 The migration system handles several major version transitions: 

 Version Migration Functions Areas Affected 0.8.0 `migrate0_8_0()` Hotkeys, Control Items, Menu Bar Sections 0.10.0 `migrate0_10_0()` Control Items 0.10.1 `migrate0_10_1()` Control Items, Corruption fixes 0.11.10 `migrate0_11_10()` Appearance Configuration 
 Each migration function performs targeted updates to specific aspects of the application's data model, ensuring backward compatibility while enabling new features. 
 Sources: Ice/Utilities/MigrationManager.swift 53-304 

### Migration Results and Error Handling 

 The migration system uses a structured approach to handling results and errors: 

```
 
```

 This structured error handling system allows Ice to gracefully handle migration issues, provide appropriate user feedback, and maintain detailed logs for troubleshooting. 
 Sources: Ice/Utilities/MigrationManager.swift 340-389 

## Menu Bar Appearance Configuration 

 Ice implements a versioned configuration system for menu bar appearance, allowing for increasingly sophisticated customization options while maintaining backward compatibility. 

### Configuration Versions 

```
 
```

 Version 1 (V1) used a single configuration for all appearance states, while Version 2 (V2) introduced separate configurations for light mode, dark mode, and a static configuration, with dynamic switching capability. 
 Sources: 
 
- Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV1.swift 9-160 

- Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV2.swift 9-155 

 

### Dynamic Appearance Features 

 Ice's appearance system dynamically adapts to system appearance changes and desktop wallpaper colors. The `LayoutBarStyle` view modifier demonstrates this functionality: 

```
 
```

 Sources: Ice/UI/ViewModifiers/LayoutBarStyle.swift 8-56 

## Other Technical Features 

 Ice implements several other technical features that enhance its functionality and user experience. These are covered in more detail in their respective wiki pages: 

### Image Caching System 

 The application implements a dedicated image caching system to efficiently manage menu bar item images. This reduces CPU and memory usage when working with numerous menu bar items.
See Image Caching for detailed information. 

### Window Management 

 Ice employs custom window management techniques to create and control specialized UI elements like the Ice Bar and settings windows.
See Window Management for detailed information. 

### Updates and Notifications 

 The application includes systems for checking and applying updates, as well as notifying users of important changes.
See Updates and Notifications for detailed information. 

### Data Migration 

 As detailed above, Ice includes a robust data migration system to handle configuration changes between versions.
See Data Migration for more comprehensive coverage. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Technical Features 
- Purpose and Scope 
- Data Migration System 
- Migration Architecture 
- Version-Specific Migrations 
- Migration Results and Error Handling 
- Menu Bar Appearance Configuration 
- Configuration Versions 
- Dynamic Appearance Features 
- Other Technical Features 
- Image Caching System 
- Window Management 
- Updates and Notifications 
- Data Migration

---

_DeepWiki section: 6.1-image-caching_

Relevant source files 
 
- Ice/Main/AppDelegate.swift 

- Ice/MenuBar/ControlItem/ControlItem.swift 

- Ice/MenuBar/ControlItem/ControlItemImage.swift 

- Ice/MenuBar/ControlItem/ControlItemImageSet.swift 

- Ice/MenuBar/MenuBarItems/MenuBarItem.swift 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 

- Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift 

- Ice/Permissions/Permission.swift 

- Ice/Permissions/PermissionsManager.swift 

- Ice/Permissions/PermissionsView.swift 

- Ice/Permissions/PermissionsWindow.swift 

- Ice/Utilities/BindingExposable.swift 

- Ice/Utilities/ScreenCapture.swift 

 
 

## Purpose and Scope 

 The Image Caching system in Ice is responsible for capturing, storing, and managing images of menu bar items. This system enables Ice to display visual representations of menu bar items in alternative interfaces like the Ice Bar and in settings windows without requiring constant screen captures. The caching system is particularly important for showing hidden menu bar items, which would otherwise be invisible to the user. 
 This page focuses on the technical implementation of menu bar item image caching. For information about general menu bar management, see Menu Bar Management . 

## System Overview 

 The Image Caching system captures images of menu bar items using screen recording capabilities, stores them in memory, and makes them available throughout the application. The system is designed to update intelligently, only capturing new images when necessary to minimize resource usage. 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 1-34 

- Ice/Utilities/ScreenCapture.swift 1-25 

- Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift 1-29 

 

## Key Components 

### MenuBarItemImageCache 

 The central component of the image caching system is the `MenuBarItemImageCache` class. This class is responsible for managing the lifecycle of cached images, determining when updates are needed, and performing screen captures. 
 Key properties of `MenuBarItemImageCache`: 
 
- `images`: A dictionary mapping `MenuBarItemInfo` objects to `CGImage` objects

- `screen`: The screen where the cached images were captured

- `menuBarHeight`: The height of the menu bar when images were captured

- `appState`: Reference to the shared app state

 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 10-36 

- Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift 7-29 

 

### ScreenCapture Utilities 

 The `ScreenCapture` enum provides utilities for capturing screen content, particularly focused on menu bar items. It handles checking permissions, requesting permissions, and capturing window images. 

```
 
```

 Sources: 
 
- Ice/Utilities/ScreenCapture.swift 9-83 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 102-190 

 

### Required Permissions 

 The image caching system requires screen recording permissions to function properly. This is managed through the `ScreenRecordingPermission` class. 

```
 
```

 Sources: 
 
- Ice/Permissions/Permission.swift 15-114 

- Ice/Permissions/Permission.swift 140-158 

- Ice/Permissions/PermissionsManager.swift 11-42 

 

## Caching Process 

 The image caching process is designed to be efficient and only update when necessary. The system employs several strategies to reduce unnecessary screen captures. 

### When Caching Occurs 

 The cache updates in the following scenarios: 
 
- Every 3 seconds (minimum interval)

- When the active space or screen parameters change

- When the average menu bar color changes

- When the cached items change

 
 However, updates are throttled to prevent excessive resource usage. 

### Conditions for Skipping Updates 

 The cache will skip updates under certain conditions: 
 
- When Ice Bar is not visible and the app is not frontmost

- When Ice Bar is not visible and Settings is not visible

- When Ice Bar is not visible, Settings is visible but not on the Menu Bar Layout page

- When an item is currently being moved

- When an item was recently moved

 

### Update Process 

 When an update is needed, the cache: 
 
- Identifies which menu bar sections need updating

- Uses `ScreenCapture` to capture images of each item

- Processes and crops the images

- Stores them in the `images` dictionary

 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 38-75 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 102-190 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 226-281 

 

## Image Capture Implementation 

### Capturing Images 

 The actual image capturing is handled by the `createImages` method of `MenuBarItemImageCache`. This method: 
 
- Retrieves the menu bar items for a specific section

- Collects window IDs and frames for each item

- Attempts to capture a composite image of all windows at once

- If composite capture fails, falls back to capturing each item individually

- Crops the images to the correct dimensions

- Returns a dictionary mapping `MenuBarItemInfo` to the captured images

 

### Composite vs. Individual Capture 

 The system prefers to capture all items in a single composite image, which is more efficient. However, if this fails (which can happen for various reasons), it falls back to capturing each item individually. 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 102-190 

- Ice/Utilities/ScreenCapture.swift 57-82 

 

## Storage and Integration 

### Image Storage 

 The cached images are stored in a dictionary property of the `MenuBarItemImageCache` class: 

```
 
```

 Using `MenuBarItemInfo` as keys allows for efficient lookup of images for specific menu bar items. The use of `@Published` ensures that any view observing the cache is updated when the images change. 

### Integration with App State 

 The image cache is integrated into the app's state management system. The `AppState` class contains an instance of `MenuBarItemImageCache` and is responsible for initializing and providing access to it throughout the application. 

```
 
```

 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 12 

- Ice/Main/AppDelegate.swift 10-11 

 

## Error Handling and Edge Cases 

 The image caching system includes several mechanisms to handle errors and edge cases: 
 
- 
 Permission Checking : Before attempting to capture images, the system checks if screen recording permissions are granted. 

```
 
```

- 
 Fallback Mechanism : If composite image capture fails, the system falls back to capturing items individually. 

```
 
```

- 
 Cache Failure Detection : The `cacheFailed` method checks if caching failed for a specific section. 

- 
 Permissions UI : If screen recording permissions aren't granted, the app displays a permissions window to guide the user. 

 
 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 65-69 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 161-186 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 85-98 

- Ice/Permissions/PermissionsView.swift 1-196 

 

## Performance Considerations 

 The image caching system implements several strategies to maintain performance: 
 
- 
 Throttling : Cache updates are throttled to prevent excessive CPU usage. 

```
 
```

- 
 Background Processing : Image capturing is performed in a background task. 

```
 
```

- 
 Conditional Updates : The cache only updates when necessary, based on various conditions. 

- 
 Efficient Storage : Images are stored in a dictionary for O(1) lookup time. 

 
 Sources: 
 
- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 60 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 65-70 

- Ice/MenuBar/MenuBarItems/MenuBarItemImageCache.swift 226-255 

 

## Summary 

 The Image Caching system in Ice provides an efficient way to capture, store, and manage visual representations of menu bar items. By intelligently updating the cache and using screen capture capabilities, it enables features like the Ice Bar and Menu Bar Layout settings to display menu bar items that might otherwise be hidden from view. The system balances performance considerations with the need for up-to-date visuals, ensuring a smooth user experience throughout the application. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Image Caching 
- Purpose and Scope 
- System Overview 
- Key Components 
- MenuBarItemImageCache 
- ScreenCapture Utilities 
- Required Permissions 
- Caching Process 
- When Caching Occurs 
- Conditions for Skipping Updates 
- Update Process 
- Image Capture Implementation 
- Capturing Images 
- Composite vs. Individual Capture 
- Storage and Integration 
- Image Storage 
- Integration with App State 
- Error Handling and Edge Cases 
- Performance Considerations 
- Summary

---

_DeepWiki section: 6.2-window-management_

Relevant source files 
 
- Ice/Utilities/WindowInfo.swift 

 
 
 This document covers the window management system in Ice, focusing on how the application interacts with and retrieves information about windows in macOS. The window management system is particularly important for Ice's ability to detect and interact with menu bar items, which are technically implemented as windows in macOS. 

## Purpose and Scope 

 The window management system in Ice provides capabilities to: 
 
- Retrieve information about windows in the system

- Identify special windows like the menu bar window

- Detect menu bar items (which are implemented as windows)

- Filter windows based on various criteria

- Support Ice's core functionality of menu bar management

 
 This document focuses on window management capabilities rather than the menu bar item management implementation. For information about how Ice manages menu bar items specifically, see Menu Bar Management . 

## WindowInfo Structure 

 The core component of the window management system is the `WindowInfo` struct, which encapsulates information about macOS windows and provides methods to retrieve window lists. 

```
 
```

 The `WindowInfo` struct provides several key properties: 
 
- `windowID`: A unique identifier for the window

- `frame`: The position and size of the window in screen coordinates

- `layer`: The z-order layer of the window (used to identify menu bar items)

- `ownerPID` and `ownerName`: Information about the application that owns the window

- Various computed properties that provide higher-level information about the window

 
 Sources: Ice/Utilities/WindowInfo.swift 9-74 

## Initialization and Window Information Retrieval 

 `WindowInfo` instances can be created from a `CGWindowID` or from dictionaries provided by Core Graphics window list APIs. 

```
 
```

 The initialization process: 
 
- Takes a window ID or window dictionary as input

- Uses Core Graphics APIs to retrieve detailed window information

- Parses various window properties into a structured Swift object

- Creates a `WindowInfo` instance with all the relevant window data

 
 Sources: Ice/Utilities/WindowInfo.swift 76-119 

## Window List Retrieval 

 The `WindowInfo` struct provides several static methods to retrieve lists of windows from the system: 

```
 
```

 These window retrieval functions allow Ice to: 
 
- Get all windows in the system with `getAllWindows()`

- Get only on-screen windows with `getOnScreenWindows()`

- Get windows above or below a specific window

- Filter the results based on criteria like excluding desktop windows

 
 Sources: Ice/Utilities/WindowInfo.swift 186-257 

## Special Window Detection 

 Ice provides specialized functions to detect important system windows, particularly the menu bar window and wallpaper window: 

```
 
```

 The ability to detect these special windows is critical for Ice's functionality: 
 
- The menu bar window detection enables Ice to properly position UI elements relative to the menu bar

- Wallpaper window detection helps with screen coordinates and appearance management

 
 Sources: Ice/Utilities/WindowInfo.swift 259-283 

## Menu Bar Item Detection 

 One of the most important use cases of the window management system is to detect menu bar items, which are implemented as windows in macOS. 

```
 
```

 Ice detects menu bar items by checking if a window's layer matches `kCGStatusWindowLevel`, which is the window level used for status items in the menu bar. This allows Ice to: 
 
- Identify which windows represent menu bar items

- Track menu bar items for hiding, showing, and interacting with them

- Monitor changes to menu bar items

 
 Sources: Ice/Utilities/WindowInfo.swift 61-63 

## Window Comparison and Hashing 

 The `WindowInfo` struct implements `Equatable` and `Hashable` protocols, allowing window instances to be compared and used in collections like Sets and Dictionaries. 

```
 
```

 These implementations compare all relevant properties of windows to determine equality, which is important for: 
 
- Tracking window changes over time

- Maintaining collections of windows

- Detecting when windows are added, removed, or modified

 
 Sources: Ice/Utilities/WindowInfo.swift 285-319 

## Integration with the Ice Architecture 

 The window management system integrates with Ice's architecture by providing foundational capabilities for the menu bar management features: 

```
 
```

 Window management functions as a low-level system that supports higher-level menu bar management features: 
 
- `WindowInfo` provides raw access to window information

- Menu bar window detection positions UI elements correctly

- Menu bar item detection identifies the items to be managed

- Higher-level components like `MenuBarManager` use this information to implement Ice's core functionality

 
 Sources: Ice/Utilities/WindowInfo.swift 

## Window Management in Action 

 The window management system operates in the background to support Ice's functionality: 
 
- When Ice starts up, it uses window management functions to detect the menu bar and its items

- As the user interacts with the system, window information is continually refreshed

- Menu bar items are tracked through their window representations

- When displaying the Ice Bar or search panel, window information helps with positioning relative to the menu bar

- Window layer information helps identify which windows are menu bar items

 

## Technical Implementation Details 

 The window management system relies heavily on Core Graphics APIs to retrieve and process window information: 

 API Function Purpose in Ice `CGWindowListCopyWindowInfo` Retrieve raw window data `CGWindowListCreateDescriptionFromArray` Get specific window information `kCGStatusWindowLevel` Identify menu bar items `kCGWindowLayer` Determine window z-order `kCGWindowOwnerPID` Identify which application owns a window 
 The system uses various options and filters to get precisely the window information needed: 
 
- `CGWindowListOption.optionAll` to get all windows

- `CGWindowListOption.optionOnScreenOnly` to get only visible windows

- `CGWindowListOption.optionOnScreenAboveWindow`/`optionOnScreenBelowWindow` for relative positioning

 
 Sources: Ice/Utilities/WindowInfo.swift 169-177 

## Conclusion 

 The window management system provides the foundation upon which many of Ice's core features are built. By abstracting the low-level Core Graphics window APIs into a more manageable Swift interface, it enables Ice to effectively interact with menu bar items and implement its menu bar management functionality. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Window Management 
- Purpose and Scope 
- WindowInfo Structure 
- Initialization and Window Information Retrieval 
- Window List Retrieval 
- Special Window Detection 
- Menu Bar Item Detection 
- Window Comparison and Hashing 
- Integration with the Ice Architecture 
- Window Management in Action 
- Technical Implementation Details 
- Conclusion

---

_DeepWiki section: 6.3-updates-and-notifications_

Relevant source files 
 
- Ice/Settings/SettingsPanes/UpdatesSettingsPane.swift 

- Ice/Updates/UpdatesManager.swift 

- Ice/UserNotifications/UserNotificationIdentifier.swift 

- Ice/UserNotifications/UserNotificationManager.swift 

 
 
 This document details the update management and notification systems in Ice. It explains how the application checks for updates, notifies users of available updates, and how users can configure update behavior. 

## 1. Overview 

 Ice implements a robust update system using the Sparkle framework and integrates with macOS's native notification system to inform users about new versions. The update system provides both automatic and manual update capabilities, with configurable user preferences. 

```
 
```

 Sources: Ice/Updates/UpdatesManager.swift Ice/UserNotifications/UserNotificationManager.swift Ice/Settings/SettingsPanes/UpdatesSettingsPane.swift 

## 2. Updates Management 

### 2.1 UpdatesManager 

 The `UpdatesManager` class serves as the central component for handling application updates. It wraps Sparkle's functionality and exposes it to the rest of the application. 
 Key responsibilities: 
 
- Checking for updates (automatic and manual)

- Managing update preferences

- Notifying users of available updates

- Handling update installation

 

```
 
```

 Sources: Ice/Updates/UpdatesManager.swift 11-92 

### 2.2 Integration with Sparkle Framework 

 Ice uses the Sparkle framework to manage the entire update lifecycle. Sparkle handles: 
 
- Checking for updates against a remote appcast

- Downloading update packages

- Verifying update signatures

- Installing updates

 
 The `UpdatesManager` initializes a `SPUStandardUpdaterController` in its setup phase and configures it with appropriate delegates to customize behavior: 

```
 
```

 Sources: Ice/Updates/UpdatesManager.swift 22-26 

## 3. Notification System 

### 3.1 UserNotificationManager 

 The `UserNotificationManager` class handles system notifications for the application, including update-related notifications. 
 Key responsibilities: 
 
- Requesting notification authorization

- Creating and scheduling notifications

- Managing notification delivery

- Handling user interactions with notifications

 

```
 
```

 Sources: Ice/UserNotifications/UserNotificationManager.swift 9-90 Ice/UserNotifications/UserNotificationIdentifier.swift 

### 3.2 Update Notifications 

 When a new update is detected, the system can notify the user through a system notification if the app is not in focus: 

```
 
```

 Sources: Ice/Updates/UpdatesManager.swift 119-141 Ice/UserNotifications/UserNotificationManager.swift 62-84 

## 4. User Interface 

### 4.1 Update Settings UI 

 The update settings are exposed to users through the `UpdatesSettingsPane` in the application's settings window. This UI allows users to configure: 
 
- Whether to automatically check for updates

- Whether to automatically download updates

- Manual update checks with a "Check for Updates" button

- View when the last update check occurred

 

```
 
```

 Sources: Ice/Settings/SettingsPanes/UpdatesSettingsPane.swift 8-71 

## 5. Update Workflow 

 The update workflow in Ice involves both automatic and manual processes, with user-configurable settings determining the behavior: 

```
 
```

 Sources: Ice/Updates/UpdatesManager.swift 56-91 Ice/Updates/UpdatesManager.swift 94-102 

### 5.1 Update Notification Handling 

 When an update is found: 
 
- If the app is active and in focus, the update dialog is shown directly

- Otherwise, a system notification is created to inform the user

- Clicking the notification will:
 
 Bring the app to the foreground

- Open the settings window

- Trigger the update process

 
 
 
 This approach ensures users are always informed about updates without disrupting their workflow. 
 Sources: Ice/Updates/UpdatesManager.swift 119-141 Ice/UserNotifications/UserNotificationManager.swift 62-84 

## 6. System Architecture 

 The updates and notifications system integrates with the larger application architecture through the central `AppState`: 

```
 
```

 This design allows the update and notification components to: 
 
- Access other app systems when needed

- Be initialized and configured centrally

- Coordinate with each other through the central app state

 
 Sources: Ice/Updates/UpdatesManager.swift 19 Ice/UserNotifications/UserNotificationManager.swift 12 

## 7. Summary 

 Ice's update and notification system provides a comprehensive solution for keeping the application up-to-date while respecting user preferences. By leveraging the Sparkle framework and macOS's notification system, it offers: 
 
- Configurable automatic update checking and downloading

- Manual update checking through the UI

- Non-intrusive update notifications

- Seamless update installation

 
 The system is designed to be reliable, user-friendly, and minimally disruptive to the user's workflow. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Updates and Notifications 
- 1. Overview 
- 2. Updates Management 
- 2.1 UpdatesManager 
- 2.2 Integration with Sparkle Framework 
- 3. Notification System 
- 3.1 UserNotificationManager 
- 3.2 Update Notifications 
- 4. User Interface 
- 4.1 Update Settings UI 
- 5. Update Workflow 
- 5.1 Update Notification Handling 
- 6. System Architecture 
- 7. Summary

---

_DeepWiki section: 6.4-data-migration_

Relevant source files 
 
- Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV1.swift 

- Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV2.swift 

- Ice/UI/ViewModifiers/LayoutBarStyle.swift 

- Ice/Utilities/MigrationManager.swift 

 
 

## Purpose and Overview 

 This document details the data migration system used in Ice to ensure user settings and configurations are properly updated when upgrading between different versions of the application. The migration system handles the transformation of persisted data structures as they evolve across application releases, ensuring backward compatibility while allowing the application architecture to advance. 
 Sources: Ice/Utilities/MigrationManager.swift 8-13 

## Migration Architecture 

 The data migration system is built around the `MigrationManager` struct, which orchestrates the migration process for different versions of the application. The system is designed to run only the necessary migrations based on which migrations have already been performed. 

```
 
```

 Sources: Ice/Utilities/MigrationManager.swift 8-13 Ice/Utilities/MigrationManager.swift 343-348 Ice/Utilities/MigrationManager.swift 353-389 

## Migration Process Flow 

 The migration process is initiated when the application starts up. The `migrateAll` static method is called, which executes all necessary migrations in sequence, checking for each if it has already been performed. 

```
 
```

 Sources: Ice/Utilities/MigrationManager.swift 17-50 

## Version-Specific Migrations 

 The migration system handles specific migrations for different application versions. Each version migration addresses particular changes in the application's data structures. 

### 0.8.0 Migration 

 This migration includes three main operations: 
 
- Hotkey Migration : Moves hotkeys from being stored in menu bar sections to stand-alone data

- Control Items Migration : Updates the serialized representations of control items

- Section Migration : Removes deprecated section data from UserDefaults

 
 Sources: Ice/Utilities/MigrationManager.swift 55-177 

### 0.10.0 Migration 

 This migration focuses on updating control item identifiers to a new format. 
 Sources: Ice/Utilities/MigrationManager.swift 180-205 

### 0.10.1 Migration 

 This migration addresses issues with control item visibility and preferred positions, resetting them if corrupted data is detected. It also shows an alert to users if their menu bar items had to be reset. 
 Sources: Ice/Utilities/MigrationManager.swift 207-252 

### 0.11.10 Migration 

 This migration converts the menu bar appearance configuration from `MenuBarAppearanceConfigurationV1` to `MenuBarAppearanceConfigurationV2`, which introduces separate configurations for light and dark mode. 

```
 
```

 Sources: Ice/Utilities/MigrationManager.swift 255-303 Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV1.swift Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV2.swift 

## Configuration Version Transition 

 A key aspect of the 0.11.10 migration is the transition from a single appearance configuration to a configuration that supports different settings for light and dark mode. 

```
 
```

 Sources: Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV1.swift Ice/MenuBar/Appearance/Configurations/MenuBarAppearanceConfigurationV2.swift 

## Error Handling and Results 

 The migration system includes comprehensive error handling to manage failures during the migration process. 

### Migration Results 

 Each migration operation can return one of three results: 

 Result Type Description Handling `success` Migration completed successfully Continue normal operation `successButShowAlert` Migration successful but user notification needed Display an alert to the user `failureAndLogError` Migration failed Log the error for diagnosis 
 Sources: Ice/Utilities/MigrationManager.swift 343-348 

### Error Types 

 The migration system defines several error types to provide context for failures: 

```
 
```

 Sources: Ice/Utilities/MigrationManager.swift 353-389 

## Helper Functions 

 The migration system includes several helper functions to facilitate the migration process: 
 
- `performAll` : Executes multiple migration blocks and combines any errors

- `getMenuBarSectionArray` : Retrieves the menu bar section data from UserDefaults

- `logError` : Logs migration errors using the application's logging system

 
 Sources: Ice/Utilities/MigrationManager.swift 307-338 Ice/Utilities/MigrationManager.swift 48-50 

## Integration with AppState 

 The migration manager integrates with the application's central state through the `AppState` reference. This allows the migration process to update various aspects of the application state as needed. 

```
 
```

 Sources: Ice/Utilities/MigrationManager.swift 10 Ice/Utilities/MigrationManager.swift 103-112 Ice/Utilities/MigrationManager.swift 273-302 

## Summary 

 The Data Migration system in Ice ensures a smooth transition between different versions of the application by systematically updating persisted data structures. By tracking which migrations have been performed and handling errors gracefully, the system maintains data integrity while allowing the application architecture to evolve. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Data Migration 
- Purpose and Overview 
- Migration Architecture 
- Migration Process Flow 
- Version-Specific Migrations 
- 0.8.0 Migration 
- 0.10.0 Migration 
- 0.10.1 Migration 
- 0.11.10 Migration 
- Configuration Version Transition 
- Error Handling and Results 
- Migration Results 
- Error Types 
- Helper Functions 
- Integration with AppState 
- Summary

---

_DeepWiki section: 7-developer-reference_

Relevant source files 
 
- .github/ISSUE_TEMPLATE/bug_report.yml 

- .github/ISSUE_TEMPLATE/feature_request.yml 

- Ice.xcodeproj/project.pbxproj 

- Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 

- Ice/Ice.entitlements 

- Ice/Info.plist 

- Ice/Resources/Acknowledgements.pdf 

- Ice/Resources/Acknowledgements.rtf 

 
 
 This document provides technical information for developers who want to contribute to or understand the Ice application codebase. It covers the development environment, project dependencies, architecture considerations, and practical guidance for working with the codebase. 
 For information about Ice's core systems, see Core Systems . For details on specific features like the Ice Bar, see Menu Bar Features . 

## Development Environment 

 Ice is a macOS application written in Swift using SwiftUI and AppKit frameworks, built with Xcode. The application targets macOS 14.0 and newer. 

### Key System Requirements 

 
- macOS: Monterey (14.0) or newer

- Xcode: Latest version recommended

- Swift: Swift 5

 
 Sources: Ice.xcodeproj/project.pbxproj 235-236 Ice.xcodeproj/project.pbxproj 292-293 

## Project Dependencies 

 Ice uses several third-party dependencies managed through Swift Package Manager (SPM). Understanding these dependencies is essential for developers working on the codebase. 

### Core Dependencies Diagram 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 391-430 Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 3-48 

### Dependencies Details 

 Dependency Purpose Repository Version AXSwift Accessibility API wrapper for retrieving and controlling menu bar items tmandry/AXSwift 0.3.2 CompactSlider UI component for slider controls in settings buh/CompactSlider 1.1.6 Ifrit Fuzzy search functionality for menu bar item search ukushu/Ifrit 2.0.3 LaunchAtLogin Simple API for enabling launch at login sindresorhus/LaunchAtLogin-Modern 1.1.0 Sparkle Framework for software updates sparkle-project/Sparkle 2.6.4 
 Sources: Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 3-48 Ice/Resources/Acknowledgements.rtf 14-114 

## Application Permissions 

 Ice requires specific macOS permissions to function properly: 

```
 
```

 Sources: Ice/Ice.entitlements 1-11 

### Entitlements 

 The application is not sandboxed to allow the necessary system interaction: 

```
`<key>com.apple.security.app-sandbox</key>
<false/>
<key>com.apple.security.files.user-selected.read-only</key>
<true/>
`
```

 Sources: Ice/Ice.entitlements 4-9 

## Code Quality and Conventions 

### SwiftLint Integration 

 The project includes SwiftLint to enforce code style and catch common Swift errors: 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 149-168 

## Contribution Workflow 

### Issue Reporting 

 The project uses structured templates for bug reports and feature requests: 

```
 
```

 Sources: .github/ISSUE_TEMPLATE/bug_report.yml 1-47 .github/ISSUE_TEMPLATE/feature_request.yml 1-24 

## Updates System 

 Ice implements an update mechanism using the Sparkle framework: 

```
 
```

 Sources: Ice/Info.plist 5-8 Ice.xcodeproj/project.pbxproj 391-398 

## Architecture Relationships 

 The following diagram illustrates how the various dependencies connect to Ice's core systems: 

```
 
```

## Recommended Development Practices 

### Building and Running 

 
- 
 Clone the repository: 

```
`git clone https://github.com/jordanbaird/Ice.git
`
```

- 
 Open the Xcode project: 

```
`open Ice.xcodeproj
`
```

- 
 Build and run using Xcode's standard build process (⌘R) 

 

### Code Contribution Workflow 

 
- Fork the repository

- Create a feature branch

- Make changes following the established coding style

- Add appropriate tests

- Submit a pull request with a clear description of changes

 

## Permissions Model 

 Ice requires permissions to function fully. The table below explains the required permissions and their purposes: 

 Permission Purpose Impact if Missing Accessibility Control and manipulate menu bar items Cannot hide/show menu bar items Screen Recording Capture menu bar images, detect colors Cannot show item previews in Ice Bar, reduced appearance customization 

## License Information 

 Ice's third-party dependencies all use the MIT license, which allows for use, modification, and distribution with minimal restrictions. 
 Sources: Ice/Resources/Acknowledgements.rtf 22-114 Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Developer Reference 
- Development Environment 
- Key System Requirements 
- Project Dependencies 
- Core Dependencies Diagram 
- Dependencies Details 
- Application Permissions 
- Entitlements 
- Code Quality and Conventions 
- SwiftLint Integration 
- Contribution Workflow 
- Issue Reporting 
- Updates System 
- Architecture Relationships 
- Recommended Development Practices 
- Building and Running 
- Code Contribution Workflow 
- Permissions Model 
- License Information

---

_DeepWiki section: 7.1-project-dependencies_

Relevant source files 
 
- Ice.xcodeproj/project.pbxproj 

- Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 

- Ice/Ice.entitlements 

- Ice/Info.plist 

- Ice/Resources/Acknowledgements.pdf 

- Ice/Resources/Acknowledgements.rtf 

 
 
 This page documents the external libraries and frameworks that Ice relies on for its functionality. These dependencies handle various aspects of the application, from accessibility features to user interface components, enabling Ice to deliver its menu bar management capabilities efficiently. 

## Overview of Dependencies 

 Ice incorporates several carefully selected third-party libraries to provide essential functionality without reinventing the wheel. Each dependency serves a specific purpose in the application architecture. 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 1-460 Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 1-51 

## Dependency Integration 

 Ice uses Swift Package Manager (SPM) for dependency management. The dependencies are defined in the project file and resolved to specific versions. 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 124-129 Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 1-51 

## Detailed Dependency Information 

### AXSwift 

 Repository : https 

 Version : 0.3.2

 License : MIT 
 AXSwift is a Swift wrapper for macOS's Accessibility APIs. It provides a more Swift-friendly interface for working with accessibility features, which are essential for Ice's menu bar manipulation capabilities. 
 Usage in Ice : 
 
- Monitors and controls menu bar items

- Interacts with system UI elements that aren't normally accessible to applications

- Enables detection and manipulation of menu bar items across different applications

 
 Sources: Ice.xcodeproj/project.pbxproj 12 Ice.xcodeproj/project.pbxproj 407-413 

### LaunchAtLogin 

 Repository : https 

 Version : 1.1.0

 License : MIT 
 LaunchAtLogin is a modern Swift package that simplifies the implementation of "launch at login" functionality in macOS applications. 
 Usage in Ice : 
 
- Provides a simple API for enabling/disabling launching at login

- Used in the settings interface to allow users to configure automatic startup

- Handles the complexities of launch services and login items

 
 Sources: Ice.xcodeproj/project.pbxproj 11 Ice.xcodeproj/project.pbxproj 399-405 

### Sparkle 

 Repository : https 

 Version : 2.6.4

 License : MIT (with components under BSD and other licenses) 
 Sparkle is a well-established framework for adding automatic update functionality to macOS applications. It handles the entire update process, from checking for updates to installing them. 
 Usage in Ice : 
 
- Checks for application updates

- Downloads update packages

- Installs updates with minimal user intervention

- Provides update notifications

- Configured with update feed URL specified in Info.plist

 
 Sources: Ice.xcodeproj/project.pbxproj 10 Ice.xcodeproj/project.pbxproj 391-397 Ice/Info.plist 5-6 

### CompactSlider 

 Repository : https 

 Version : 1.1.6

 License : MIT 
 CompactSlider is a SwiftUI component that provides a compact, customizable slider control. 
 Usage in Ice : 
 
- Used in the settings interface for value adjustments

- Likely employed in the appearance customization features

- Provides a consistent and refined UI element for slider controls

 
 Sources: Ice.xcodeproj/project.pbxproj 13 Ice.xcodeproj/project.pbxproj 415-421 

### IfritStatic 

 Repository : https 

 Version : 2.0.3

 License : MIT 
 Ifrit is a fuzzy search library for Swift, providing powerful search capabilities with tolerance for typos and partial matches. 
 Usage in Ice : 
 
- Powers the menu bar search functionality

- Enables users to quickly find menu bar items

- Provides fuzzy matching for more intuitive search results

 
 Sources: Ice.xcodeproj/project.pbxproj 14 Ice.xcodeproj/project.pbxproj 423-429 

## Dependencies in Application Architecture 

 The following diagram illustrates how each dependency integrates with different components of the Ice application architecture: 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 1-460 

## Build System Integration 

 Ice uses Swift Package Manager for dependency management, with dependencies specified in the Xcode project file. The project also includes SwiftLint as a build phase for code quality control. 

### SwiftLint Integration 

 While not a runtime dependency, SwiftLint is used during development to enforce coding style and best practices: 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 149-168 

## License Information 

 All dependencies used in Ice are available under the MIT license, which is permissive and allows for use in both open-source and commercial applications. The acknowledgments document included with Ice provides the full license text for each dependency. 

 Dependency License Repository AXSwift MIT https://github.com/tmandry/AXSwift LaunchAtLogin MIT https://github.com/sindresorhus/LaunchAtLogin-Modern Sparkle MIT* https://github.com/sparkle-project/Sparkle CompactSlider MIT https://github.com/buh/CompactSlider IfritStatic MIT https://github.com/ukushu/Ifrit 
 *Sparkle includes components under additional licenses (BSD and others) which are detailed in the acknowledgments. 
 Sources: Ice/Resources/Acknowledgements.rtf 1-174 Ice/Resources/Acknowledgements.pdf 1-485 

## Dependency Version Management 

 Ice specifies dependency versions using semantic versioning constraints, generally using the "up to next major" constraint to allow for compatible updates while avoiding potentially breaking changes. 

```
 
```

 Sources: Ice.xcodeproj/project.pbxproj 390-431 Ice.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved 1-51 

## Conclusion 

 The dependencies used in Ice were carefully chosen to provide specific functionality while maintaining good performance and reliability. Each dependency is integrated into the application architecture to fulfill specific requirements, from accessibility control to update management. Understanding these dependencies is important for developers who want to contribute to Ice or understand how it works internally. 
 For information on how to contribute to Ice, see Contributing Guidelines . Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Project Dependencies 
- Overview of Dependencies 
- Dependency Integration 
- Detailed Dependency Information 
- AXSwift 
- LaunchAtLogin 
- Sparkle 
- CompactSlider 
- IfritStatic 
- Dependencies in Application Architecture 
- Build System Integration 
- SwiftLint Integration 
- License Information 
- Dependency Version Management 
- Conclusion

---

_DeepWiki section: 7.2-contributing-guidelines_

Relevant source files 
 
- .github/ISSUE_TEMPLATE/bug_report.yml 

- .github/ISSUE_TEMPLATE/feature_request.yml 

 
 
 This document outlines how to contribute to the Ice project. Whether you're reporting bugs, requesting features, or submitting code changes, these guidelines will help ensure a smooth contribution process. For information about the project's overall architecture and systems, see Project Architecture . 

## Issue Reporting 

 Ice uses structured templates for bug reports and feature requests to ensure all necessary information is captured. 

### Bug Reports 

 When experiencing an issue with Ice, follow these steps to submit a bug report: 
 
- Search the issue tracker to ensure the bug hasn't already been reported

- Use the bug report template to provide:
 
 A clear description of the bug

- Steps to reproduce

- Ice version

- macOS version

- Optional screenshots or recordings

 
 
 

```
 
```

 Sources: .github/ISSUE_TEMPLATE/bug_report.yml 

### Feature Requests 

 When you have an idea for improving Ice, follow these steps: 
 
- Search the issue tracker to ensure the feature hasn't already been requested

- Use the feature request template to provide:
 
 A clear description of the requested feature

- Optional screenshots or mockups illustrating the feature

 
 
 

```
 
```

 Sources: .github/ISSUE_TEMPLATE/feature_request.yml 

## Development Environment Setup 

### Prerequisites 

 To contribute to Ice, you'll need: 
 
- macOS (preferably the latest version)

- Xcode (latest stable version recommended)

- Git

 

### Setting Up Local Development 

```
 
```

 
- Fork the Ice repository on GitHub

- Clone your fork locally

```
`git clone https://github.com/YOUR-USERNAME/Ice.git
`
```

- Set up the upstream remote 

```
`git remote add upstream https://github.com/jordanbaird/Ice.git
`
```

- Create a feature branch 

```
`git checkout -b feature/your-feature-name
`
```

 

## Code Style Guidelines 

 When contributing to Ice, please follow these general code style practices: 

### Swift Style 

 
- Follow Apple's Swift API Design Guidelines 

- Use descriptive variable and function names

- Include documentation comments for public APIs

- Keep functions small and focused

 

### Project Structure 

 Ice follows a manager-based architecture with centralized state management: 

```
 
```

## Pull Request Process 

 When submitting changes to Ice, follow these guidelines: 
 
- Create a descriptive PR title that summarizes the changes

- Reference any related issues using GitHub's issue linking syntax (e.g., "Fixes #123")

- Describe your changes in detail, explaining:
 
 What problem your PR solves

- How your implementation works

- Any design decisions you made

 
 
- Include testing information 
 
 How you tested your changes

- Any edge cases considered

 
 
 

```
 
```

### Commit Messages 

 Write clear commit messages that explain the "what" and "why" of your changes: 
 
- Use the imperative mood ("Add feature" not "Added feature")

- First line is a concise summary (max 72 characters)

- Separate the summary from the body with a blank line

- Use the body to explain what and why, not how

 

## Testing Guidelines 

 Testing is essential for maintaining Ice's quality and stability: 
 
- 
 Manual Testing 
 
 Test your changes across different macOS versions if possible

- Verify interactions with various menu bar items

- Check both normal operation and edge cases

 
 
- 
 System Integration Testing 
 
 Since Ice interacts with macOS system APIs, test thoroughly in different scenarios:
 
 With different menu bar items present

- With various permissions granted/denied

- With multiple displays if applicable

 
 
 
 
 

```
 
```

## Documentation 

 Good documentation helps maintain Ice's codebase and assists future contributors: 
 
- 
 Code Comments 
 
 Document complex algorithms

- Explain "why" rather than "what" when appropriate

- Add documentation comments to public APIs

 
 
- 
 Update Wiki 
 
 If your changes affect functionality documented in the wiki, update the relevant pages

- Add new wiki pages for significant new features

 
 
 

## Debugging Tips 

 Since Ice interacts deeply with macOS systems, debugging can be challenging: 
 
- 
 Permissions 
 
 Issues often relate to permissions (Accessibility, Screen Recording)

- Test both with and without permissions

 
 
- 
 Menu Bar Access 
 
 Use the `EventManager` and `MenuBarManager` classes to debug menu bar interactions

- The `AppState` provides access to all subsystems for debugging

 
 
- 
 Logging 
 
 Add appropriate logging to help diagnose issues

- Remove debug logging before submitting PRs

 
 
 

## Conclusion 

 Thank you for contributing to Ice! By following these guidelines, you help maintain a high-quality codebase and make the contribution process smoother for everyone involved. Dismiss Refresh this wiki Enter email to refresh 
### On this page
 - Contributing Guidelines 
- Issue Reporting 
- Bug Reports 
- Feature Requests 
- Development Environment Setup 
- Prerequisites 
- Setting Up Local Development 
- Code Style Guidelines 
- Swift Style 
- Project Structure 
- Pull Request Process 
- Commit Messages 
- Testing Guidelines 
- Documentation 
- Debugging Tips 
- Conclusion
