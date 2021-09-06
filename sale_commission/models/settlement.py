# Copyright 2014-2018 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, exceptions, fields, models, _
from odoo.exceptions import UserError
from odoo import tools
from psycopg2.extensions import AsIs

class Settlement(models.Model):
    _name = "sale.commission.settlement"
    _description = "Settlement"

    def _default_currency(self):
        return self.env.user.company_id.currency_id.id

    name = fields.Char('Name')
    total = fields.Float(compute="_compute_total", readonly=True, store=True)
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")
    agent = fields.Many2one(
        comodel_name="res.partner", domain="[('agent', '=', True)]")
    agent_type = fields.Selection(related='agent.agent_type')
    # lines = fields.One2many(
    lines = fields.One2many(
        comodel_name="sale.commission.settlement.line",
        inverse_name="settlement", string="Settlement lines", readonly=True)
    state = fields.Selection(
        selection=[("settled", "Settled"),
                   ("invoiced", "Invoiced"),
                   ("cancel", "Canceled"),
                   ("except_invoice", "Invoice exception")], string="State",
        readonly=True, default="settled")
    invoice = fields.Many2one(
        comodel_name="account.invoice", string="Generated invoice",
        readonly=True)
    # origin = fields.Char(string="Origin")
    currency_id = fields.Many2one(
        comodel_name='res.currency', readonly=True,
        default=_default_currency)
    company_id = fields.Many2one(
        comodel_name='res.company',
        default=lambda self: self.env.user.company_id,
        required=True
    )

    @api.depends('lines', 'lines.settled_amount')
    def _compute_total(self):
        for record in self:
            record.total = sum(x.settled_amount for x in record.lines)

    @api.multi
    def action_cancel(self):
        if any(x.state != 'settled' for x in self):
            raise exceptions.Warning(
                _('Cannot cancel an invoiced settlement.'))
        self.write({'state': 'cancel'})

    @api.multi
    def unlink(self):
        """Allow to delete only cancelled settlements"""
        if any(x.state == 'invoiced' for x in self):
            raise exceptions.Warning(
                _("You can't delete invoiced settlements."))
        return super(Settlement, self).unlink()

    @api.multi
    def action_invoice(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Make invoice'),
            'res_model': 'sale.commission.make.invoice',
            'view_type': 'form',
            'target': 'new',
            'view_mode': 'form',
            'context': {'settlement_ids': self.ids}
        }

    def _prepare_invoice_header(self, settlement, journal, date=False):
        invoice = self.env['account.invoice'].new({
            'partner_id': settlement.agent.id,
            'type': ('in_invoice' if journal.type == 'purchase' else
                     'in_refund'),
            'date_invoice': date,
            'journal_id': journal.id,
            'company_id': settlement.company_id.id,
            'state': 'draft',
        })
        # Get other invoice values from onchanges
        invoice._onchange_partner_id()
        invoice._onchange_journal_id()
        return invoice._convert_to_write(invoice._cache)

    def _prepare_invoice_line(self, settlement, invoice, product):
        invoice_line = self.env['account.invoice.line'].new({
            'invoice_id': invoice.id,
            'product_id': product.id,
            'quantity': 1,
        })
        # Get other invoice line values from product onchange

        invoice_line._onchange_product_id()
        invoice_line_vals = invoice_line._convert_to_write(invoice_line._cache)
        # Put commission fee
        if invoice.type == 'in_refund':
            invoice_line_vals['price_unit'] = -settlement.total
        else:
            invoice_line_vals['price_unit'] = settlement.total
        # Put period string
        lang = self.env['res.lang'].search(
            [('code', '=', invoice.partner_id.lang or
              self.env.context.get('lang', 'en_US'))])
        date_from = fields.Date.from_string(settlement.date_from)
        date_to = fields.Date.from_string(settlement.date_to)
        invoice_line_vals['name'] += "\n" + _('Period: from %s to %s') % (
            date_from.strftime(lang.date_format),
            date_to.strftime(lang.date_format))
        return invoice_line_vals

    def _add_extra_invoice_lines(self, settlement):
        """Hook for adding extra invoice lines.
        :param settlement: Source settlement.
        :return: List of dictionaries with the extra lines.
        """
        return []
        
    def create_invoice_header(self, journal, date):
        """Hook that can be used in order to group invoices or
        find open invoices
        """
        invoice_vals = self._prepare_invoice_header(self, journal, date=date)
        return self.env['account.invoice'].create(invoice_vals)

    @api.multi
    def make_invoices(self, journal, product, date=False):
        invoice_line_obj = self.env['account.invoice.line']
        for settlement in self:
            # select the proper journal according to settlement's amount
            # considering _add_extra_invoice_lines sum of values
            extra_invoice_lines = self._add_extra_invoice_lines(settlement)
            invoice = settlement.create_invoice_header(journal, date)
            invoice_line_vals = self._prepare_invoice_line(
                settlement, invoice, product)
            invoice_line_obj.create(invoice_line_vals)
            invoice.compute_taxes()
            for invoice_line_vals in extra_invoice_lines:
                invoice_line_obj.create(invoice_line_vals)
            settlement.write({
                'state': 'invoiced',
                'invoice': invoice.id,
            })
        if self.env.context.get('no_check_negative', False):
            return
        for settlement in self:
            if settlement.invoice.amount_total < 0:
                raise UserError(_('Value cannot be negative'))


class SettlementLine(models.Model):
    _name = "sale.commission.settlement.line"
    _description = "Line of a commission settlement"

    settlement = fields.Many2one(
        "sale.commission.settlement", readonly=True, ondelete="cascade",
        required=True)
    agent_line = fields.Many2many(
        comodel_name='account.invoice.line.agent',
        relation='settlement_agent_line_rel', column1='settlement_id',
        column2='agent_line_id', required=True)
    date = fields.Date(related="agent_line.invoice_date", store=True)
    invoice_line = fields.Many2one(
        comodel_name='account.invoice.line', store=True,
        related='agent_line.object_id')
    invoice = fields.Many2one(
        comodel_name='account.invoice', store=True, string="Invoice",
        related='invoice_line.invoice_id')
    origin = fields.Char(
        string="Origin", store=True,
        related='invoice_line.origin') 
    # origin = "teste"
    customer = fields.Char(
        string="Customer", store=True,
        related='invoice_line.partner_id.name'
    )     
    # customer = fields.Char(
    #     string="Customer", default = "teste"
    # )     
    # customer = "customer"
    agent = fields.Many2one(
        comodel_name="res.partner", readonly=True, related="agent_line.agent",
        store=True)
    settled_amount = fields.Monetary(
        related="agent_line.amount", readonly=True, store=True)
        
    currency_id = fields.Many2one(
        related="agent_line.currency_id",
        store=True,
        readonly=True,
    )
    commission = fields.Many2one(
        comodel_name="sale.commission", related="agent_line.commission")
    company_id = fields.Many2one(
        comodel_name='res.company',
        related='settlement.company_id',
    )

    @api.depends('settlement.lines')
    def _compute_commission_total(self):
        for record in self:
            record.commission_total = 0.0
            for line in record.agent_line:
                record.commission_total += sum(x.amount for x in line)

    # commission_total = fields.Float(
    #     string="Commissions",
    #     compute="_compute_commission_total",
    #     store=True,
    #     )            
    comm_total = fields.Float(
        comodel_name='invoice_vals.commission_total',
        string="Comissão Total", store=True,
        related='invoice.commission_total'
        )   

    @api.constrains('settlement', 'agent_line')
    def _check_company(self):
        for record in self:
            for line in record.agent_line:
                if line.company_id != record.company_id:
                    raise UserError(_("Company must be the same"))


class ComissaoTeste(models.Model):
    _name = "sale.commission.teste"
    _description = "Sale Commission Analysis Report"
    _auto = False
    _rec_name = 'commission_id'

    

    @api.model
    def _get_selection_invoice_state(self):
        return self.env['account.invoice'].fields_get(
            allfields=['state'])['state']['selection']

    invoice_state = fields.Selection(selection='_get_selection_invoice_state',
                                     string='Invoice Status', readonly=True)
    date_invoice = fields.Date('Date Invoice', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Partner', readonly=True)
    agent_id = fields.Many2one('res.partner', 'Agent', readonly=True)
    categ_id = fields.Many2one(
        'product.category',
        'Category of Product',
        readonly=True)
    # product_id = fields.Many2one('product.product', 'Product', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit of Measure', readonly=True)
    quantity = fields.Float('# of Qty', readonly=True)
    price_unit = fields.Float('Price unit', readonly=True)
    price_subtotal = fields.Float('Price subtotal', readonly=True)
    price_subtotal_signed = fields.Float(
        string='Price subtotal signed',
        readonly=True,
    )
    percentage = fields.Integer('Percentage of commission', readonly=True)
    amount = fields.Float('Amount', readonly=True)
    invoice_line_id = fields.Many2one(
        'account.invoice.line',
        'Invoice line',
        readonly=True)
    settled = fields.Boolean('Settled', readonly=True)
    commission_id = fields.Many2one(
        'sale.commission',
        'Sale commission',
        readonly=True)
    invoice_origin = fields.Many2one('account_invoice.origin', 'SO Origin', readonly=True)
    

    def _select(self):
        select_str = """
            SELECT MIN(aila.id) AS id,
            ai.partner_id AS partner_id,
            ai.state AS invoice_state,
            ai.origin AS invoice_origin,
            ai.date_invoice AS date_invoice,
            ail.company_id AS company_id,
            rp.id AS agent_id,
            pt.categ_id AS categ_id,
            pt.uom_id AS uom_id,
            SUM(ail.quantity) AS quantity,
            AVG(ail.price_unit) AS price_unit,
            SUM(ail.price_subtotal) AS price_subtotal,
            SUM(ail.price_subtotal_signed) AS price_subtotal_signed,
            AVG(sc.fix_qty) AS percentage,
            SUM(aila.amount) AS amount,
            ail.id AS invoice_line_id,
            aila.settled AS settled,
            aila.commission AS commission_id
        """
        # ail.product_id AS product_id,
        return select_str

    def _from(self):
        from_str = """
            account_invoice_line_agent aila
            LEFT JOIN account_invoice_line ail ON ail.id = aila.object_id
            INNER JOIN account_invoice ai ON ai.id = ail.invoice_id
            LEFT JOIN sale_commission sc ON sc.id = aila.commission
            LEFT JOIN product_product pp ON pp.id = ail.product_id
            INNER JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN res_partner rp ON aila.agent = rp.id
        """
        return from_str

    def _group_by(self):
        group_by_str = """
            GROUP BY ai.partner_id,
            ai.state,
            ai.origin,
            ai.date_invoice,
            ail.company_id,
            rp.id,
            pt.categ_id,
            pt.uom_id,
            ail.id,
            aila.settled,
            aila.commission
        """
        # ail.product_id,
        return group_by_str

    @api.model
    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        self._cr.execute(
            "CREATE or REPLACE VIEW %s AS ( %s FROM ( %s ) %s )", (
                AsIs(self._table),
                AsIs(self._select()),
                AsIs(self._from()),
                AsIs(self._group_by())
            ),
        )
