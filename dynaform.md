# DynaForm - A dynamically generated form for filling out Jinja2 templates

DynaForm is a simple web application for filling out details in a Jinja2 template file.

## Goal
* DynaForm is a Python Flask app running inside a small Podman container. Templates can be
uploaded as a file or inputed as raw text in a text area. It stores no information, it only
takes and input and renders and output.

* The app is server rendered using Bootstrap 5 which is locally baked in to the container

* The app uses Flask WTForms to render dynamic and safe HTML forms.

* The app uses the application factory pattern served by Gunicorn 

* The app reads the .j2 file, extracts the defined variables and creates a simple HTML form
using the defined variables. Using a special syntax, the user can define variables to have
specific HTML form types like text, number or radio buttons. 

* If a variable does not follow the defined syntax, the app rejects the input tempate

* The syntax is a leading letter before each variable name which corresponds to a form input type:
    * 'S_' = text
    * 'P_' = password
    * 'N_' = number
    * 'B_' = checkbox

* The variabe name is separated by undescore after the defined prefix:
    * `S_username`
    * `P_password`
    * `N_user_age`
    * `B_admin`
    
* For variable names with multiple words, the first word is capitalised in the rendered form page.

## Desired features

* Radio button griyos should be supported somehow by grouping variables together

* The form should be able to have conditional variables displayed if the user checks a checkbox variable.
For example: Checking `B_admin` unfolds additional form inputs for `S_admin_name` and `P_admin_pass`.
