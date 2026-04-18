please generate a document in the planning/ui/ directory to describe all th efunctions, current and planned, of the overture application, in whatever detail makes sense for generating mockups of the various pages of the ui

Use the source, in addition to this initial list of features I have in mind for the UI. please work with me to tune this list if I've missed something useful.

- the ui will start up with no active sessions. There should be a landing page that is also the 'new session' page, with a simple form:
  - a directory, fuzzy-searchable in the user's home directory. only index git repositories
    - don't index below any directory in which you find a .git directory, but do allow an input that does not match the indexed directories
  - a resume button
    - a conditional dropdown of active sessions appears when this is checked. The dropdown is populated with the names of the directories under .overture/sessions/ in the specified project directory. Ignore uuid-named directories
  - a start button that creates a session tab 

- for each active session, a tab at the top
for each tab, sections on the left, each of which brings up a new page:
- DAG - graphical diagram of the WU DAG, colored to clearly identify WUs in progress, complete, pending, failed. text in each box is the name of the WU
  - click to see a modal popup with WU description, state, solution domain, and links to agent output and to activate diff modal if state is complete 
- context gathering
  - interactive session with the context-gatherer agent
  - should be easy to provide:
    - multiple lines of text
    - files from repository, fuzzy-searchable with an '@' prefix or via a separate form input that enables the user to search for a file and add to context
    - uploaded files from elsewhere on disk
  - input box disappears when user confirms context gathering is complete.
    - a button allows the user to reengage the context gatherer agent, but only until the work begins. If any WU is not marked 'pending', context and plan are committed and context can no longer change
  - User sees conversation with agent, but an accordion UI element allows the user to hide the conversation and see the context.md file in the session directory. Context can be manually edited as plain markdown and saved from the web UI
- plan 
  - rendered plan, with outline linking to each WU, each WU with its own header and subheaders for ACs, domain, status
  - A button allows the user to recall the planner for a reshape if they want to suggest changes or reshape after changing the context file
- agents
  - agent output, organized chronologically, for all WUs as they're completed
    - e.g., implementer, then reviewer, then potentially reviewer or implementer then reviewer again to address any issues
  - display freeform agent output, files changed, highlight any domain breaches
  - link for each completed WU to activate the diff modal for that WU's merged commit

additional, reusable modal: show code diffs in a popup modal with a link in various places