# -*- coding: utf-8 -*-
# Copyright 2022 IZI PT Solusi Usaha Mudah
from odoo import models, fields, api, _


class IZITable(models.Model):
    _name = 'izi.table'
    _description = 'IZI Table'
    _order = 'name'

    name = fields.Char(string='Name', required=True)
    table_name = fields.Char('Table Name')
    source_id = fields.Many2one('izi.data.source', string='Data Source', required=True, ondelete='cascade')
    model_id = fields.Many2one('ir.model', string='Model')
    field_ids = fields.One2many('izi.table.field', 'table_id', string='Fields')
    analysis_ids = fields.One2many(comodel_name='izi.analysis', inverse_name='table_id', string='Analysis')
    active = fields.Boolean('Active', default=True)
    db_query = fields.Text('Database Query')
    is_stored = fields.Boolean(string='Is Stored')
    cron_id = fields.Many2one(comodel_name='ir.cron', string='Related Cron')
    cron_user_id = fields.Many2one(comodel_name='res.users', string='Scheduler User', related='cron_id.user_id')
    cron_interval_number = fields.Integer(string='Interval Number', default=1, related='cron_id.interval_number')
    cron_nextcall = fields.Datetime(string='Next Execution Date', related='cron_id.nextcall')
    cron_interval_type = fields.Selection(string='Interval Unit', related='cron_id.interval_type')
    cron_active = fields.Boolean(string='Active Scheduler', related='cron_id.active')

    _sql_constraints = [
        ('name_source_unique', 'unique(name, source_id)', 'Name Already Exist.')
    ]

    """
    - Can be generated from Odoo model
    - Can be generated directly from table in external data sources
    - Can be generated by joining two or more Odoo models
    - Can be generated by joining two or more tables from external data source
    - Maybe, just maybe, Insyaa Allah in the future, can be generated by joining tables from Odoo model and external
    data sources. Insane!
    """

    @api.model
    def create(self, vals):
        rec = super(IZITable, self).create(vals)
        if rec.db_query:
            rec.get_table_fields()
        if rec.is_stored and not rec.cron_id:
            rec.delete_schema()
            rec.insert_schema()
            table_cron = self.env['ir.cron'].sudo().create({
                'name': 'Scheduller %s' % rec.name,
                'model_id': self.env.ref('%s.model_%s' % (self._module, '_'.join(self._name.split('.')))).id,
                'state': 'code',
                'code': False,
                'interval_number': 5,
                'interval_type': 'minutes',
                'numbercall': -1,
                'active': False,
            })
            rec.cron_id = table_cron.id
        return rec

    def write(self, vals):
        if 'is_stored' in vals:
            if self.is_stored is True and vals['is_stored'] is False:
                res = super(IZITable, self).write(vals)
                self.destroy_schema()
                if self.cron_id:
                    self.cron_id.sudo().unlink()
                return res
            elif self.is_stored is False and vals['is_stored'] is True:
                table_cron = self.env['ir.cron'].sudo().create({
                    'name': 'Scheduller %s' % self.name,
                    'model_id': self.env.ref('%s.model_%s' % (self._module, '_'.join(self._name.split('.')))).id,
                    'state': 'code',
                    'code': False,
                    'interval_number': 5,
                    'interval_type': 'minutes',
                    'numbercall': -1,
                    'active': False,
                })
                vals['cron_id'] = table_cron.id
                res = super(IZITable, self).write(vals)
                self.get_table_fields()
                self.destroy_schema()
                self.build_schema()
                self.delete_schema()
                self.insert_schema()
                return res
        res = super(IZITable, self).write(vals)
        if 'db_query' in vals:
            self.get_table_fields()
        return res

    def execute_values(self, query, datas, cols, fetch=False):
        self.ensure_one()

        if len(datas) == 0:
            return datas

        all_data = []
        fmt = ','.join(['%s' for x in range(cols)])
        for i in datas:
            all_data.append(
                self.env.cr.mogrify(
                    self.env.cr.mogrify("(%s)" % fmt), i
                ).decode())
        data_sql = ','.join(all_data)
        self.env.cr.execute(self.env.cr.mogrify(query % (data_sql,)))

        print('data rowcount', len(datas))
        print('pg operation rowcount', self.env.cr.rowcount)

        if fetch:
            return self.env.cr.fetchall()

    def get_table_fields(self):
        self.ensure_one()

        Field = self.env['izi.table.field']

        # Get existing fields based on table
        field_by_name = {}
        for field_record in Field.search([('table_id', '=', self.id)]):
            field_by_name[field_record.field_name] = field_record

        # Check
        table_query = self.table_name
        if self.db_query:
            table_query = '(%s) tbl_query' % (self.db_query)
        table_query = table_query.replace(';', '')

        func_check_query = getattr(self.source_id, 'check_query_%s' % self.source_id.type)
        func_check_query(**{
            'query': table_query,
        })

        func_get_table_fields = getattr(self, 'get_table_fields_%s' % self.source_id.type)
        result = func_get_table_fields(**{
            'field_by_name': field_by_name,
            'table_query': table_query,
        })

        field_by_name = result.get('field_by_name')

        for field_name in field_by_name:
            for dimension in field_by_name[field_name].analysis_dimension_ids:
                dimension.unlink()
            for metric in field_by_name[field_name].analysis_metric_ids:
                metric.unlink()
            field_by_name[field_name].unlink()

        self.destroy_schema()
        self.build_schema()

    def get_table_datas(self):
        self.ensure_one()

        res_data = []
        res_metrics = []
        res_dimensions = []
        res_fields = []
        res_values = []

        # Build Metric Query
        field_query = ''
        table_query = self.table_name
        field_queries = []

        for field in self.field_ids:
            field_queries.append('%s as "%s"' % (field.field_name, field.name))
            res_fields.append(field.name)
        field_query = ', '.join(field_queries)

        # Check
        if self.db_query:
            table_query = '(%s) tbl_query' % (self.db_query)
        table_query = table_query.replace(';', '')

        # Build Query
        query = '''
            SELECT %s
            FROM %s;
        ''' % (field_query, table_query)

        func_check_query = getattr(self.source_id, 'check_query_%s' % self.source_id.type)
        func_check_query(**{
            'query': query,
        })

        func_get_table_datas = getattr(self, 'get_table_datas_%s' % self.source_id.type)
        result = func_get_table_datas(**{
            'query': query,
        })

        res_data = result.get('res_data')

        for record in res_data:
            res_value = []
            for key in record:
                res_value.append(record[key])
            res_values.append(res_value)

        result = {
            'data': res_data,
            'metrics': res_metrics,
            'dimensions': res_dimensions,
            'fields': res_fields,
            'values': res_values,
        }

        if 'test_query' not in self._context:
            return result
        else:
            title = _("Successfully Get Data")
            message = _("""
                Your table name or table query looks fine!
                Sample Data:
                %s
            """ % (str(result.get('data')[0]) if result.get('data') else str(result.get('data'))))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': title,
                    'message': message,
                    'sticky': False,
                }
            }

    def build_schema(self):
        for izi_table in self:
            if izi_table.is_stored is True and izi_table.db_query is not False:
                table_name = (izi_table.name.replace(' ', '_') + '_' + izi_table.source_id.type).lower()
                create_table_query = "CREATE TABLE IF NOT EXISTS %s (" % table_name
                list_fields = []
                for izi_field in izi_table.field_ids:
                    list_fields.append(izi_field)
                for field in list_fields:
                    create_table_query += "%s %s" % (field.field_name, field.field_type_origin.upper())
                    if field != list_fields[-1]:
                        create_table_query += ", "
                    else:
                        create_table_query += ")"
                self.env.cr.execute(create_table_query)

    def destroy_schema(self):
        for izi_table in self:
            if izi_table.is_stored is True and izi_table.db_query is not False:
                table_name = (izi_table.name.replace(' ', '_') + '_' + izi_table.source_id.type).lower()
                drop_table_query = "DROP TABLE IF EXISTS %s" % table_name
                self.env.cr.execute(drop_table_query)

    def insert_schema(self):
        for izi_table in self:
            if izi_table.is_stored is True and izi_table.db_query is not False:
                table_name = (izi_table.name.replace(' ', '_') + '_' + izi_table.source_id.type).lower()
                insert_table_query = "INSERT INTO %s (" % table_name
                list_fields = []
                for izi_field in izi_table.field_ids:
                    list_fields.append(izi_field)
                for field in list_fields:
                    insert_table_query += field.field_name
                    if field != list_fields[-1]:
                        insert_table_query += ", "
                    else:
                        insert_table_query += ") VALUES %s "
                result = izi_table.get_table_datas()
                insert_data = []
                for data in result.get('values'):
                    insert_data.append(tuple(data))
                izi_table.execute_values(insert_table_query, insert_data, len(list_fields))

    def delete_schema(self):
        for izi_table in self:
            if izi_table.is_stored is True and izi_table.db_query is not False:
                table_name = (izi_table.name.replace(' ', '_') + '_' + izi_table.source_id.type).lower()
                delete_table_query = "DELETE FROM %s" % table_name
                self.env.cr.execute(delete_table_query)

    def update_schema(self):
        for izi_table in self:
            if izi_table.is_stored is True and izi_table.db_query is not False:
                izi_table.delete_schema()
                izi_table.insert_schema()

        title = _("Successfully Update Schema Datas")
        message = _("Your stored table datas are successfully updated!")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
            }
        }


class IZITableField(models.Model):
    _name = 'izi.table.field'
    _description = 'IZI Table Field'
    _order = 'name'

    name = fields.Char(string='Name', required=True)
    field_name = fields.Char('Field Name')
    field_type = fields.Char('Field Type')
    field_type_origin = fields.Char(string='Field Type Origin')
    field_id = fields.Many2one('ir.model.fields', string='Field')
    table_id = fields.Many2one('izi.table', string='Table', required=True, ondelete='cascade')
    foreign_table = fields.Char(string='Foreign Table')
    foreign_column = fields.Char(string='Foreign Column')
    analysis_metric_ids = fields.One2many(comodel_name='izi.analysis.metric',
                                          inverse_name='field_id', string='Analysis Metric')
    analysis_dimension_ids = fields.One2many(comodel_name='izi.analysis.dimension',
                                             inverse_name='field_id', string='Analysis Dimension')

    _sql_constraints = [
        ('name_source_unique', 'unique(name, table_id)', 'Name Already Exist.')
    ]

    def get_field_type_mapping(self, type_origin, source_type):
        field_mapping = self.env['izi.table.field.mapping'].search(
            [('name', '=', type_origin), ('source_type', '=', source_type)])
        if field_mapping:
            return field_mapping.type_mapping
        else:
            return None

    @api.onchange('field_type_origin')
    def onchange_field_type_origin(self):
        field_mapping = self.env['izi.table.field.mapping'].search(
            [('name', '=', self.field_type_origin), ('source_type', '=', self.table_id.source_id.type)])
        if field_mapping:
            self.field_type = field_mapping.type_mapping
        else:
            self.field_type = None


class IZITableFieldMapping(models.Model):
    _name = 'izi.table.field.mapping'
    _description = 'IZI Table Field Mapping'

    name = fields.Char(string='Type Origin', required=True)
    type_mapping = fields.Char(string='Type Mapping', required=True)
    source_type = fields.Char(string='Source Type', required=True)

    _sql_constraints = [
        ('name_source_unique', 'unique(name, source_type)', 'Type Origin Already Exist.')
    ]
